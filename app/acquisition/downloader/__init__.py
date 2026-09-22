"""app/acquisition/downloader/__init__.py"""
from app.acquisition.downloader.client import DocumentDownloader, DownloadResult, default_downloader

__all__ = ["DocumentDownloader", "DownloadResult", "default_downloader"]
