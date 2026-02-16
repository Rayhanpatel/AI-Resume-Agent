"""
Job URL Extractor - Fetches and extracts text from any job URL
"""
import re
import logging
import httpx
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# Block internal/private networks and cloud metadata endpoints (SSRF prevention)
BLOCKED_PATTERNS = [
    re.compile(r"^https?://localhost", re.IGNORECASE),
    re.compile(r"^https?://127\."),
    re.compile(r"^https?://10\."),
    re.compile(r"^https?://192\.168\."),
    re.compile(r"^https?://172\.(1[6-9]|2[0-9]|3[0-1])\."),
    re.compile(r"^https?://\[::1\]"),
    re.compile(r"^https?://169\.254\."),  # AWS/Cloud metadata endpoint
    re.compile(r"^file://", re.IGNORECASE),
]


def is_url(text: str) -> bool:
    """Check if text looks like a URL"""
    if not text:
        return False
    return text.strip().lower().startswith(("http://", "https://"))


def is_blocked_url(url: str) -> bool:
    """Check if URL matches blocked patterns (SSRF prevention)"""
    return any(p.match(url) for p in BLOCKED_PATTERNS)


async def extract_from_url(
    http_client: httpx.AsyncClient,
    url: str,
    timeout: float = 15.0
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract job description text from URL.
    
    Args:
        http_client: Async HTTP client
        url: Job posting URL
        timeout: Request timeout in seconds
        
    Returns:
        Tuple of (extracted_text, error_message) - one will be None
    """
    url = url.strip()
    
    if is_blocked_url(url):
        return None, "This URL is not accessible. Please paste the job text."
    
    try:
        response = await http_client.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )
        
        # Validate final URL after redirects (SSRF via open redirect prevention)
        final_url = str(response.url)
        if is_blocked_url(final_url):
            return None, "Redirected to inaccessible URL. Please paste the job text."
        
        if response.status_code == 403:
            return None, "Page requires login. Please paste the job text instead."
        
        if response.status_code == 404:
            return None, "Page not found. Please check the URL or paste the job text."
        
        if response.status_code != 200:
            return None, f"Could not load page (HTTP {response.status_code}). Please paste the job text."
        
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/json" not in content_type:
            return None, "This link doesn't contain readable text. Please paste the job description."
        
        html = response.text
        
        if len(html) > 500000:  # 500KB max
            return None, "Page is too large. Please paste the relevant job description."
        
        text = _clean_html(html)
        
        if len(text) < 100:
            return None, "Could not extract job content. Please paste it manually."
        
        # Truncate to reasonable size
        return text[:15000], None
        
    except httpx.TimeoutException:
        return None, "Page took too long to load. Please paste the job text instead."
    except httpx.ConnectError:
        return None, "Could not connect to the page. Please paste the job text."
    except Exception as e:
        logger.warning(f"URL extraction error for {url}: {e}")
        return None, "Failed to load page. Please paste the job description."


def _clean_html(html: str) -> str:
    """Remove HTML tags and extract readable text"""
    # Remove script elements
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove style elements  
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav, header, footer
    html = re.sub(r'<(nav|header|footer)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r'<!--.*?-->', ' ', html, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode common HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()
