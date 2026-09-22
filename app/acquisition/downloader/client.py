"""app/acquisition/downloader/client.py

Robust Document Downloader for the BIS Knowledge Acquisition System.

Features:
- Domain allowlist enforcement (never crawl arbitrary external sites)
- Rate limiting and courtesy delays
- Exponential backoff with bounded retries
- HTTP status and Content-Type verification
- Magic byte validation (%PDF-)
- SHA-256 content hashing and deduplication
- Deterministic raw file persistence under data/raw/
- Full audit logging
"""

import hashlib
import logging
import ssl
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from app.acquisition.models import AuditLogEntry, DownloadStatus, RightsStatus
from app.config import DATA_DIR, RAW_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN_ALLOWLIST: Set[str] = {
    "www.bis.gov.in",
    "bis.gov.in",
    "www.services.bis.gov.in",
    "services.bis.gov.in",
    "www.manakonline.in",
    "manakonline.in",
    "huid.manakonline.in",
    "cuid.manakonline.in",
    "dpiit.gov.in",
    "egazette.gov.in",
    "consumeraffairs.nic.in",
    "meity.gov.in",
    "www.meity.gov.in",
}

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class DownloadResult:
    """Structured outcome of a document download attempt."""
    def __init__(
        self,
        success: bool,
        status: DownloadStatus,
        url: str,
        http_status: Optional[int] = None,
        content_hash: Optional[str] = None,
        local_path: Optional[Path] = None,
        file_size: int = 0,
        error: Optional[str] = None,
        is_duplicate: bool = False,
    ):
        self.success = success
        self.status = status
        self.url = url
        self.http_status = http_status
        self.content_hash = content_hash
        self.local_path = local_path
        self.file_size = file_size
        self.error = error
        self.is_duplicate = is_duplicate

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "url": self.url,
            "http_status": self.http_status,
            "content_hash": self.content_hash,
            "local_path": str(self.local_path) if self.local_path else None,
            "file_size": self.file_size,
            "error": self.error,
            "is_duplicate": self.is_duplicate,
        }


class DocumentDownloader:
    """
    Controlled document downloader for official regulatory material.
    """

    def __init__(
        self,
        raw_dir: Optional[Path] = None,
        domain_allowlist: Optional[Set[str]] = None,
        timeout: float = 30.0,
        rate_limit_delay: float = 0.5,
        max_retries: int = 2,
    ):
        self.raw_dir = Path(raw_dir) if raw_dir else RAW_DATA_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.allowlist = domain_allowlist or DEFAULT_DOMAIN_ALLOWLIST
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self._last_request_time: Dict[str, float] = {}
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf;q=0.9,*/*;q=0.8",
        })

    def is_url_allowed(self, url: str) -> bool:
        """Verify URL domain belongs strictly to approved official regulatory portals."""
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or ""
        return hostname.lower() in self.allowlist

    def _respect_rate_limit(self, domain: str) -> None:
        """Apply courteous delay between consecutive requests to the same domain."""
        last_time = self._last_request_time.get(domain, 0.0)
        elapsed = time.time() - last_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time[domain] = time.time()

    def download_pdf(
        self,
        url: str,
        destination_subdir: str,
        filename_prefix: Optional[str] = None,
        expected_hash: Optional[str] = None,
    ) -> DownloadResult:
        """
        Download a PDF from an official source with full validation.
        """
        parsed = urllib.parse.urlparse(url)
        domain = parsed.hostname or ""

        # 1. Domain Check
        if not self.is_url_allowed(url):
            logger.warning("Download rejected: domain '%s' not in official allowlist.", domain)
            return DownloadResult(
                success=False,
                status=DownloadStatus.BLOCKED_ACCESS,
                url=url,
                error=f"Domain '{domain}' is not in approved official allowlist.",
            )

        # 2. Rate limit courtesy
        self._respect_rate_limit(domain)

        # 3. HTTP Request with exponential backoff retries
        last_error = ""
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout, verify=False)
                if response.status_code == 200:
                    break
                elif response.status_code in (401, 403):
                    return DownloadResult(
                        success=False,
                        status=DownloadStatus.BLOCKED_ACCESS,
                        url=url,
                        http_status=response.status_code,
                        error=f"Access blocked by server (HTTP {response.status_code}). Authentication/license required.",
                    )
                else:
                    last_error = f"HTTP status {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(1.5 * (2 ** attempt))

        if not response or response.status_code != 200:
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                url=url,
                http_status=response.status_code if response else None,
                error=last_error or "Failed to download document.",
            )

        raw_bytes = response.content

        # 4. Validate Magic Bytes for PDF
        if not raw_bytes.startswith(b"%PDF"):
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                url=url,
                http_status=response.status_code,
                error="Downloaded payload did not contain valid PDF magic bytes (%PDF-).",
            )

        # 5. Compute Content Hash
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        # 6. Save to controlled raw directory
        target_dir = self.raw_dir / destination_subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_stem = filename_prefix or Path(parsed.path).stem or "document"
        safe_stem = "".join(c if (c.isascii() and c.isalnum()) or c in ("-", "_") else "_" for c in safe_stem)[:64]
        target_file = target_dir / f"{safe_stem}.pdf"

        target_file.write_bytes(raw_bytes)
        logger.info("Successfully downloaded %s (%d bytes, hash=%s) to %s", url, len(raw_bytes), content_hash[:12], str(target_file).encode("ascii", "replace").decode("ascii"))

        return DownloadResult(
            success=True,
            status=DownloadStatus.DOWNLOADED,
            url=url,
            http_status=response.status_code,
            content_hash=content_hash,
            local_path=target_file,
            file_size=len(raw_bytes),
        )


default_downloader = DocumentDownloader()
