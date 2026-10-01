import csv
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


REQUESTS_PER_TARGET = 50
REQUESTS_PER_SECOND = 2
REQUEST_INTERVAL = 1 / REQUESTS_PER_SECOND
REQUEST_TIMEOUT = 120

OUTPUT_DIR = Path("results")
OUTPUT_FILE = OUTPUT_DIR / "raw_results.csv"

TARGETS = [
    {
        "name": "joblookup",
        "url": "https://joblookup.com/us/jobs/Software%20Engineer",
        "protection": "DataDome",
        "use_case": "job board",
    },
    {
        "name": "chrono24",
        "url": "https://www.chrono24.com/rolex/index.htm",
        "protection": "Cloudflare",
        "use_case": "e-commerce",
    },
    {
        "name": "reuters",
        "url": "https://www.reuters.com/world/",
        "protection": "Akamai",
        "use_case": "news",
    },
    {
        "name": "wikipedia",
        "url": "https://en.wikipedia.org/wiki/Wikipedia:Contents/Outlines",
        "protection": "Unprotected",
        "use_case": "baseline",
    },
]


def get_page_title(content):
    if not content:
        return None

    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()

    return None


def has_meaningful_content(content, title=None):
    if not content:
        return False

    text = re.sub(
        r"<script.*?</script>",
        " ",
        content,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) < 500:
        return False

    challenge_terms = [
        "checking your browser",
        "verify you are human",
        "just a moment",
        "access denied",
        "enable javascript and cookies",
        "cf-chl",
        "captcha",
    ]

    lower_text = text.lower()

    if any(term in lower_text for term in challenge_terms):
        return False

    if title and len(title) > 3:
        return True

    return len(text) >= 500


def zenrows_request(url):
    api_key = os.environ["ZENROWS_API_KEY"]

    params = {
        "url": url,
        "apikey": api_key,
        "mode": "auto",
    }

    start = time.perf_counter()

    response = requests.get(
        "https://api.zenrows.com/v1/",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    elapsed = (time.perf_counter() - start) * 1000

    return {
        "http_status": response.status_code,
        "content": response.text,
        "response_time_ms": round(elapsed, 2),
        "cost": response.headers.get("X-Request-Cost"),
        "target_status": None,
    }


def firecrawl_request(url):
    api_key = os.environ["FIRECRAWL_API_KEY"]

    payload = {
        "url": url,
        "formats": ["html"],
        "proxy": "auto",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start = time.perf_counter()

    response = requests.post(
        "https://api.firecrawl.dev/v2/scrape",
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    elapsed = (time.perf_counter() - start) * 1000

    content = ""
    target_status = None

    try:
        data = response.json()

        content = data.get("data", {}).get("html", "")

        target_status = data.get("data", {}).get("status")

    except ValueError:
        content = response.text

    return {
        "http_status": response.status_code,
        "content": content,
        "response_time_ms": round(elapsed, 2),
        "cost": None,
        "target_status": target_status,
    }


def jina_request(url):
    headers = {}

    api_key = os.getenv("JINA_API_KEY")

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    reader_url = f"https://r.jina.ai/{url}"

    start = time.perf_counter()

    response = requests.get(
        reader_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    elapsed = (time.perf_counter() - start) * 1000

    return {
        "http_status": response.status_code,
        "content": response.text,
        "response_time_ms": round(elapsed, 2),
        "cost": None,
        "target_status": None,
    }


def crawlforge_request(url):
    api_key = os.environ["CRAWLFORGE_API_KEY"]

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    start = time.perf_counter()

    response = requests.post(
        "https://www.crawlforge.dev/api/v1/tools/fetch_url",
        headers=headers,
        json={"url": url},
        timeout=REQUEST_TIMEOUT,
    )

    elapsed = (time.perf_counter() - start) * 1000

    content = ""
    target_status = None
    credits_used = None

    try:
        data = response.json()

        content = data.get("data", {}).get("content", "")

        target_status = data.get("data", {}).get("status")

        credits_used = data.get("credits_used")

    except ValueError:
        data = {}

    return {
        "http_status": response.status_code,
        "content": content,
        "response_time_ms": round(elapsed, 2),
        "cost": credits_used,
        "target_status": target_status,
    }


PROVIDERS = {
    "zenrows": zenrows_request,
    "firecrawl": firecrawl_request,
    "jina": jina_request,
    "crawlforge": crawlforge_request,
}


def run_benchmark():
    OUTPUT_DIR.mkdir(exist_ok=True)

    missing_targets = [
        target["name"]
        for target in TARGETS
        if not target["url"]
    ]

    if missing_targets:
        raise RuntimeError(
            "Missing target URLs: "
            + ", ".join(missing_targets)
        )

    fieldnames = [
        "timestamp",
        "provider",
        "target",
        "protection",
        "request_number",
        "http_status",
        "target_status",
        "success",
        "page_title",
        "content_length",
        "response_time_ms",
        "cost",
        "error",
    ]

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for provider_name, provider_request in PROVIDERS.items():

            print(f"\n=== {provider_name.upper()} ===")

            for target in TARGETS:

                print(
                    f"\nTesting {target['name']} "
                    f"({target['protection']})"
                )

                for request_number in range(
                    1,
                    REQUESTS_PER_TARGET + 1,
                ):

                    timestamp = datetime.now(
                        timezone.utc
                    ).isoformat()

                    result = {
                        "timestamp": timestamp,
                        "provider": provider_name,
                        "target": target["name"],
                        "protection": target["protection"],
                        "request_number": request_number,
                        "http_status": None,
                        "target_status": None,
                        "success": False,
                        "page_title": None,
                        "content_length": 0,
                        "response_time_ms": None,
                        "cost": None,
                        "error": None,
                    }

                    try:
                        response = provider_request(
                            target["url"]
                        )

                        content = response["content"]

                        title = get_page_title(content)

                        success = has_meaningful_content(
                            content,
                            title,
                        )

                        result.update(
                            {
                                "http_status": response[
                                    "http_status"
                                ],
                                "target_status": response[
                                    "target_status"
                                ],
                                "success": success,
                                "page_title": title,
                                "content_length": len(content),
                                "response_time_ms": response[
                                    "response_time_ms"
                                ],
                                "cost": response["cost"],
                            }
                        )

                        status = (
                            "SUCCESS"
                            if success
                            else "FAIL"
                        )

                        print(
                            f"{request_number:02d}/"
                            f"{REQUESTS_PER_TARGET} "
                            f"{status} "
                            f"HTTP {response['http_status']} "
                            f"{response['response_time_ms']:.0f}ms"
                        )

                    except Exception as exc:
                        result["error"] = str(exc)

                        print(
                            f"{request_number:02d}/"
                            f"{REQUESTS_PER_TARGET} ERROR "
                            f"{exc}"
                        )

                    writer.writerow(result)
                    file.flush()

                    if request_number < REQUESTS_PER_TARGET:
                        time.sleep(REQUEST_INTERVAL)

    print(
        f"\nBenchmark complete."
        f"\nResults saved to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    run_benchmark()
