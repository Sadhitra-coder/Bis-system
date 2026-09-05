from __future__ import annotations

from app.crawler.crawler import BisCrawler
from app.database.session import init_db


def main() -> None:
    init_db()
    crawler = BisCrawler()
    result = crawler.crawl(max_pages=20)
    print(result)


if __name__ == "__main__":
    main()
