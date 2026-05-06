import re
import urllib.parse
from html import unescape
from typing import Optional, List, Any, Tuple

try:
    from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
    from PyQt6.QtCore import QUrl
except ImportError:
    pass

from . import utils
from .collector import SourceBlock
from .bandcamp_html_extractor import extract_bandcamp_tags_from_html

def _get_config(key, default=None):
    return utils._get_config(key, default)

class SourceBase:
    """Base class for sources."""
    name: str = "base"
    
    def fetch(self, album, callback):
        """
        Fetch metadata for album.
        callback is func(source_name, SourceBlock | None, error_str | None)
        """
        raise NotImplementedError

# ---------------------------------------------------------------------------
# Bandcamp Logic (Ported/Adapted)
# ---------------------------------------------------------------------------

def extract_bandcamp_urls_from_text(text: str) -> List[str]:
    """
    Extract Bandcamp URLs from text (annotation, cover art comments, etc.).
    
    Uses regex to find URLs matching bandcamp.com patterns.
    Prefers URLs containing /album/ or /releases/.
    
    Args:
        text: Text to search for Bandcamp URLs
        
    Returns:
        List of found Bandcamp URLs (may be empty)
    """
    if not text or not isinstance(text, str):
        return []
    
    # Pattern: http:// or https:// followed by anything up to bandcamp.com and path
    # Matches URLs like: https://artist.bandcamp.com/album/name or https://artist.bandcamp.com/releases
    pattern = r"https?://[^\s]*bandcamp\.com[^\s]*"
    matches = re.findall(pattern, text, re.IGNORECASE)
    
    if not matches:
        return []
    
    # Filter to valid Bandcamp URLs and prefer album/releases URLs
    valid_urls = []
    album_urls = []
    
    for url in matches:
        url_lower = url.lower()
        # Check if it's a valid Bandcamp URL (album, EP, single, or releases page)
        # Remove trailing punctuation that might have been captured
        url = url.rstrip('.,;:!?)')
        if any(path in url_lower for path in ['/album/', '/releases', '/track/', '/ep/', '/single/']):
            if '/album/' in url_lower or '/releases' in url_lower:
                album_urls.append(url)
            else:
                valid_urls.append(url)
    
    # Return album/releases URLs first, then others
    return album_urls + valid_urls


def resolve_bandcamp_url(album, release, release_group=None) -> Tuple[Optional[str], str]:
    """
    Multi-step resolution of Bandcamp album URL.
    
    Checks in order:
    1. Release URL relations
    2. Release-group URL relations
    3. Release annotation text
    4. Cover art archive comments
    
    Args:
        album: Picard album object
        release: MusicBrainz release dict
        release_group: MusicBrainz release-group dict (optional)
        
    Returns:
        Tuple of (url_or_none, source_description)
        source_description indicates where URL was found or why it wasn't found
    """
    # Step 1: Check release URL relations
    if release and isinstance(release, dict):
        relations = release.get("relations", [])
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                
                url_obj = relation.get("url", {})
                if isinstance(url_obj, dict):
                    resource = url_obj.get("resource", "")
                else:
                    resource = relation.get("resource", "")
                
                if resource and ".bandcamp.com" in resource.lower():
                    # Check if it's an album/EP/single/releases URL
                    resource_lower = resource.lower()
                    # Accept album, EP, single, or releases page URLs
                    if any(path in resource_lower for path in ['/album/', '/releases', '/track/', '/ep/', '/single/']):
                        utils.log_info(f"UMA: bandcamp: found Bandcamp URL in release relations: {resource}")
                        return resource, "release relations"
    
    # Step 2: Check release-group URL relations
    if release_group and isinstance(release_group, dict):
        relations = release_group.get("relations", [])
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                
                url_obj = relation.get("url", {})
                if isinstance(url_obj, dict):
                    resource = url_obj.get("resource", "")
                else:
                    resource = relation.get("resource", "")
                
                if resource and ".bandcamp.com" in resource.lower():
                    resource_lower = resource.lower()
                    # Accept album, EP, single, or releases page URLs
                    if any(path in resource_lower for path in ['/album/', '/releases', '/track/', '/ep/', '/single/']):
                        utils.log_info(f"UMA: bandcamp: found Bandcamp URL in release-group relations: {resource}")
                        return resource, "release-group relations"
    
    # Step 3: Check release annotation
    # Many MB releases have Bandcamp URLs in annotation text rather than as explicit relations
    if release and isinstance(release, dict):
        annotation = release.get("annotation", "")
        if annotation and isinstance(annotation, str):
            urls = extract_bandcamp_urls_from_text(annotation)
            if urls:
                url = urls[0]  # Take first (preferentially album/releases URLs from extract function)
                utils.log_info(f"UMA: bandcamp: found Bandcamp URL in annotation: {url}")
                return url, "annotation"
    
    # Step 4: Check cover art archive comments
    # Cover Art Archive images sometimes have Bandcamp URLs in their comment field
    if release and isinstance(release, dict):
        # Check if release has cover-art-archive data
        caa_data = release.get("cover-art-archive", {})
        if isinstance(caa_data, dict):
            # Try to access images if available
            images = caa_data.get("images", [])
            if isinstance(images, list):
                for image in images:
                    if isinstance(image, dict):
                        comment = image.get("comment", "")
                        if comment and isinstance(comment, str):
                            urls = extract_bandcamp_urls_from_text(comment)
                            if urls:
                                url = urls[0]
                                utils.log_info(f"UMA: bandcamp: found Bandcamp URL in cover art comment: {url}")
                                return url, "cover art comment"
        
        # Also check if album object has cover art data
        if hasattr(album, 'metadata') and album.metadata:
            # Picard may store cover art info in metadata
            # Try common keys
            for key in ['~caa_comment', '~coverart_comment']:
                comment = album.metadata.get(key, "")
                if comment:
                    urls = extract_bandcamp_urls_from_text(str(comment))
                    if urls:
                        url = urls[0]
                        utils.log_info(f"UMA: bandcamp: found Bandcamp URL in cover art comment: {url}")
                        return url, "cover art comment"
    
    return None, "not found"


class BandcampSource(SourceBase):
    name = "bandcamp"
    
    def fetch(self, album, callback):
        """
        Fetch Bandcamp tags.
        Supports multi-step URL discovery and fallback search.
        """
        utils.log_debug("BandcampSource: fetch requested")
        
        # This method is called when no URL was found in __init__.py
        # Try to resolve URL using multi-step discovery
        # Note: release and release_group should be passed via album object or we need to get them
        
        # Get release from album if available (stored by __init__.py)
        release = getattr(album, "_uma_release", None)
        release_group = getattr(album, "_uma_release_group", None)
        
        # Fallback: try direct album attributes if needed
        if not release and hasattr(album, "release"):
            release = album.release
        if not release_group and hasattr(album, "release_group"):
            release_group = album.release_group
        
        # Log what we know about the Bandcamp context for debugging
        artist_hint = getattr(album, "_uma_band_artist", None)
        title_hint = getattr(album, "_uma_band_title", None)
        utils.log_debug(
            f"BandcampSource: fetch context - artist_hint={artist_hint!r}, "
            f"title_hint={title_hint!r}, has_release={bool(release)}, has_release_group={bool(release_group)}"
        )
        
        # Try multi-step URL resolution
        url, source = resolve_bandcamp_url(album, release, release_group)
        
        if url:
            utils.log_info(f"UMA: bandcamp: using URL {url} (found in {source})")
            self.fetch_from_url(album, url, callback)
            return

        # If still no URL, try fallback search if enabled
        fallback_enabled = _get_config("uma_bandcamp_fallback_search", False)
        if fallback_enabled:
            # Get artist and title - use more flexible metadata extraction
            artist = self._get_artist_name(album)
            album_title = self._get_album_title(album)
            
            if artist and album_title:
                utils.log_debug(f"BandcampSource: fallback search query=\"{artist} {album_title}\" (artist=\"{artist}\", title=\"{album_title}\")")
                self._fallback_search(album, artist, album_title, callback)
                return
            else:
                utils.log_debug(f"BandcampSource: insufficient metadata for fallback search (artist={bool(artist)}, title={bool(album_title)})")
        
        # All methods exhausted
        utils.log_warning(f"BandcampSource: failed to resolve Bandcamp URL (no relation, no annotation URL, no cover art URL, insufficient data for search)")
        callback(self.name, None, "no_url")
        return
    
    def _get_artist_name(self, album) -> Optional[str]:
        """
        Extract artist name from album metadata, trying multiple sources.
        
        Tries in order:
        1. album.artist_credit (if available as attribute)
        2. metadata["albumartist"]
        3. metadata["artist"]
        4. metadata["~albumartists_sort"] or metadata["~artists_sort"]
        """
        # Try album.artist_credit first (main artist name string)
        if hasattr(album, 'artist_credit') and album.artist_credit:
            return str(album.artist_credit).strip()
        
        if not hasattr(album, 'metadata') or not album.metadata:
            return None
        
        # Prefer cached artist from album_processor (MB release metadata)
        artist_hint = getattr(album, "_uma_band_artist", None)
        if artist_hint:
            return str(artist_hint).strip()
        
        # Try albumartist first, then artist from album.metadata
        if not hasattr(album, "metadata") or not album.metadata:
            return None
        
        artist = album.metadata.get("albumartist") or album.metadata.get("artist")
        if artist:
            return str(artist).strip()
        
        # Try artist_credit if available (Picard may populate this)
        artist_credit = album.metadata.get("~albumartists_sort") or album.metadata.get("~artists_sort")
        if artist_credit:
            return str(artist_credit).strip()
        
        return None
    
    def _get_album_title(self, album) -> Optional[str]:
        """
        Extract album/release title from album metadata.
        
        Tries in order:
        1. cached album._uma_band_title (from MB release metadata)
        2. album.title (if available as attribute)
        3. metadata["album"] or metadata["title"]
        """
        # Prefer cached title from album_processor (MB release metadata)
        title_hint = getattr(album, "_uma_band_title", None)
        if title_hint:
            return str(title_hint).strip()
        
        # Next, try album.title if present
        if hasattr(album, "title") and album.title:
            return str(album.title).strip()
        
        if not hasattr(album, "metadata") or not album.metadata:
            return None
        
        title = album.metadata.get("album") or album.metadata.get("title")
        if title:
            return str(title).strip()
        
        return None

    def _extract_bandcamp_url(self, album):
        """Extract Bandcamp URL from album relations."""
        # Using album metadata to find relationships
        # Note: 'album' passed here is picard.album.Album
        # accessing album.metadata["~url-bandcamp"] might work if populated,
        # otherwise iterate distinct relations (which are not always exposed easily in Album obj without iterating)
        # But we can look at album._new_album.relations or similar if available, OR we rely on what Picard gives us
        
        # Simpler approach: Check if user already has a URL in metadata
        # or iterate over `album.metadata.get_all("~url-bandcamp")`?
        # Actually standard way is `album.metadata["~relation_..."]` ? No.
        
        # Let's inspect the `bandcamp_tag_fetcher` way:
        # It iterates `album.metadata["relation"]`?
        
        # Re-using the `_extract_bandcamp_url` logic from the original file I read:
        # It looks at `release["relations"]`... wait, `fetch_bandcamp_tags` takes `album`.
        # The logic was `_extract_bandcamp_url(release)` where release comes from MB lookup.
        
        # Since we are running AFTER album load, we should have MB metadata.
        # Picard stores release info in `album['musicbrainz_albumid']` etc.
        # But relationships are tricky.
        
        # Let's try to find a URL that matches bandcamp.com in the album's known URLs
        # Often these are in `~url-relation-type`?
        
        # For now, let's look at `album.metadata.get_all("~url")` if that exists, or iterate.
        # Actually, let's trust the logic from the other plugin:
        # It seems it hooks into `register_album_metadata_processor`.
        # The `release` dict is passed to that processor? No, usually `album, metadata, release`.
        pass 
        # I will implement this in the `__init__.py` hook where `release` dict is available!
        # `fetch` will just receive the URL from `__init__.py` or we store it on AlbumContext.
        # For this class design, let's assume `fetch` takes a URL or `album` object has it attached.
        # Let's assume we pass URL to fetch to make it pure.
        return None

    def fetch_from_url(self, album, url, callback):
        """
        Fetch Bandcamp metadata from a specific URL, following HTTP redirects
        and simple HTML-level redirects if needed.

        Note: Bandcamp pages are HTML. We intentionally avoid Picard's webservice
        parsing layer here, because it may attempt to parse responses as JSON/XML.
        """
        utils.log_info(f"UMA: bandcamp: fetching metadata from URL: {url}")

        manager = getattr(album.tagger, "_network_manager", None)
        if not manager:
            manager = QNetworkAccessManager()
            album.tagger._network_manager = manager

        if not hasattr(album, "_uma_replies"):
            album._uma_replies = []

        max_http_redirects = 10
        max_html_redirects = 2

        def _finalize_with_html(final_url: str, html: str, http_depth: int, html_depth: int) -> None:
            """Follow optional HTML-level redirects, then extract tags from final HTML."""
            redirect_url = self._detect_html_redirect(final_url, html)
            if redirect_url and html_depth < max_html_redirects:
                utils.log_debug(
                    f"BandcampSource: following HTML redirect from {final_url} "
                    f"to {redirect_url} (html_depth={html_depth + 1}, http_depth={http_depth})"
                )
                _do_request(redirect_url, http_depth=http_depth, html_depth=html_depth + 1)
                return

            tags = self._extract_tags(html)
            album_title = (
                album.metadata.get("album", "unknown")
                if hasattr(album, "metadata") and album.metadata
                else "unknown"
            )
            utils.log_info(f"UMA: bandcamp: raw tags for album '{album_title}': {tags}")

            # Bandcamp provides "tags", not curated genres. We keep tags for
            # mapping and comments, but do NOT copy them into genres; final
            # genres should come from the mapping engine and curated sources
            # (e.g. Discogs), not raw SEO-like tag strings.
            block = SourceBlock(name=self.name, genres=[], tags=tags, styles=[])

            utils.log_info(
                f"UMA: bandcamp: raw genres/styles for album '{album_title}': "
                f"genres={block.genres}, styles={block.styles}"
            )
            utils.log_info(f"UMA: bandcamp: fetched metadata successfully for {final_url}")
            callback(self.name, block, None)

        def _do_request(current_url: str, http_depth: int = 0, html_depth: int = 0) -> None:
            qurl = QUrl(current_url)
            request = QNetworkRequest(qurl)
            request.setRawHeader(
                b"User-Agent",
                b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                b"AppleWebKit/537.36 (KHTML, like Gecko) "
                b"Chrome/120 Safari/537.36",
            )

            reply = manager.get(request)
            album._uma_replies.append(reply)

            def _finished():
                try:
                    if reply in album._uma_replies:
                        album._uma_replies.remove(reply)

                    if reply.error() != QNetworkReply.NetworkError.NoError:
                        error_msg = reply.errorString()
                        utils.log_warning(f"UMA: bandcamp: failed: {error_msg}")
                        callback(self.name, None, error_msg)
                        return

                    # HTTP redirect handling (Location header)
                    redirect_target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
                    if redirect_target is not None and http_depth < max_http_redirects:
                        try:
                            # redirect_target may be a QUrl or QVariant-like; normalize to string
                            if hasattr(redirect_target, "toString"):
                                target_str = str(redirect_target.toString())
                            else:
                                target_str = str(redirect_target)
                            target_str = target_str.strip()
                            if target_str:
                                base = reply.url().toString() if hasattr(reply.url(), "toString") else str(reply.url())
                                new_url = urllib.parse.urljoin(base, target_str)
                                utils.log_debug(
                                    f"BandcampSource: HTTP redirect -> {new_url} "
                                    f"(from={current_url}, http_depth={http_depth + 1})"
                                )
                                reply.deleteLater()
                                _do_request(new_url, http_depth=http_depth + 1, html_depth=html_depth)
                                return
                        except Exception:
                            # If redirect extraction fails, just continue with body
                            pass

                    final_url = reply.url().toString() if hasattr(reply.url(), "toString") else str(reply.url())
                    utils.log_debug(
                        f"BandcampSource: HTTP final URL={final_url} (http_depth={http_depth}, html_depth={html_depth})"
                    )

                    data = reply.readAll()
                    html = bytes(data).decode("utf-8", errors="ignore")
                    _finalize_with_html(final_url, html, http_depth=http_depth, html_depth=html_depth)

                except Exception as e:
                    error_msg = f"parse error: {e}"
                    utils.log_warning(f"UMA: bandcamp: failed: {error_msg}")
                    utils.log_error(f"Bandcamp parse error: {e}", exc_info=True)
                    callback(self.name, None, error_msg)
                finally:
                    reply.deleteLater()

            reply.finished.connect(_finished)

        _do_request(url, http_depth=0, html_depth=0)

    def _detect_html_redirect(self, current_url: str, html: str) -> Optional[str]:
        """
        Detect simple HTML-level redirects (meta refresh, window.location) and return target URL.

        Only handles static patterns, not arbitrary JavaScript execution.
        """
        if not html:
            return None

        # 1) Meta refresh: <meta http-equiv="refresh" content="0; url=/album/...">
        meta_match = re.search(
            r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if meta_match:
            content = meta_match.group(1)
            url_match = re.search(r"url\s*=\s*([^;]+)", content, re.IGNORECASE)
            if url_match:
                target = url_match.group(1).strip(" '\"")
                if target:
                    new_url = urllib.parse.urljoin(current_url, target)
                    utils.log_debug(
                        f"BandcampSource: meta-refresh redirect -> {new_url} "
                        f"(from content={content!r})"
                    )
                    return new_url

        # 2) JavaScript: window.location = "https://..." or window.location.href = '...'
        js_match = re.search(
            r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
            html,
            re.IGNORECASE,
        )
        if js_match:
            target = js_match.group(1).strip()
            if target:
                new_url = urllib.parse.urljoin(current_url, target)
                utils.log_debug(f"BandcampSource: JS redirect -> {new_url}")
                return new_url

        return None

    def _extract_tags(self, html):
        """Extract tags using robust extraction from legacy client if available."""
        try:
            # Try to use the robust extraction from bandcamp_tag_fetcher
            from bandcamp_tag_fetcher.client import _extract_tags_from_html, _is_valid_tag
            
            tags = _extract_tags_from_html(html)
            utils.log_debug(f"BandcampSource: Extracted {len(tags)} tags using legacy extraction")
            return tags
        except ImportError:
            # First, try the dedicated HTML extractor (most reliable for <a class="tag"> elements)
            html_tags = extract_bandcamp_tags_from_html(html)
            if html_tags:
                utils.log_debug(
                    f"BandcampSource: HTML extractor found {len(html_tags)} tags (sample={html_tags[:8]!r})"
                )
                # De-dupe with UMA normalization (keeps original case from HTML)
                out: List[str] = []
                seen = set()
                for t in html_tags:
                    norm = utils.normalize_tag(t)
                    if norm and norm not in seen:
                        seen.add(norm)
                        out.append(t.strip())
                if out:
                    return out
            
            # Fallback to simple regex/JSON extraction (adapted from bandcamp_tag_fetcher._extract_tags_regex)
            utils.log_debug("BandcampSource: HTML extractor found no tags, using extended regex/JSON fallback")
            tags: List[str] = []

            # Pattern 1: <a class="tag">text</a> or <a rel="tag">text</a>
            pattern1 = r'<a[^>]*(?:class=["\']tag["\']|rel=["\']tag["\'])[^>]*>([^<]+)</a>'
            matches1 = re.findall(pattern1, html, re.IGNORECASE)
            for m in matches1:
                tag = m.strip()
                if tag:
                    tags.append(tag)

            # Pattern 2: data-tag attribute
            pattern2 = r'data-tag=["\']([^"\']+)["\']'
            matches2 = re.findall(pattern2, html, re.IGNORECASE)
            for m in matches2:
                tag = m.strip()
                if tag:
                    tags.append(tag)

            # Pattern 3: Tags array in JSON / script blocks:  ..."tags": ["a","b","c"]...
            pattern3 = r'"tags?"\s*:\s*\[([^\]]+)\]'
            matches3 = re.findall(pattern3, html, re.IGNORECASE)
            for m in matches3:
                # Extract quoted strings from array
                for t in re.findall(r'"([^"]+)"', m):
                    t = t.strip()
                    if t:
                        tags.append(t)

            # Pattern 3b: JSON-LD "keywords" array, which often mirrors the visible tags
            # Example in Bandcamp HTML:
            #   "keywords":["Altar Records The Flying Mars","Electronic","psy-chill",...]
            keywords_pattern = r'"keywords"\s*:\s*\[([^\]]+)\]'
            kw_matches = re.findall(keywords_pattern, html, re.IGNORECASE)
            for m in kw_matches:
                for t in re.findall(r'"([^"]+)"', m):
                    t = t.strip()
                    if t:
                        tags.append(t)

            # Pattern 4: Tags as plain text in elements with class containing "tag" and label "Tags:"
            pattern4 = r'<[^>]*class=["\'][^"\']*tag[^"\']*["\'][^>]*>.*?[Tt]ags?\s*:?\s*([^<]+)</[^>]*>'
            matches4 = re.findall(pattern4, html, re.IGNORECASE | re.DOTALL)
            for m in matches4:
                text = re.sub(r'<[^>]+>', '', m)
                text = unescape(text).strip()
                # Remove leading "Tags:" label if present
                text = re.sub(r'^[Tt]ags?\s*:\s*', '', text, flags=re.I)
                if text:
                    # Split on dashes and whitespace into individual tokens
                    parts = re.split(r'\s*[-–—,;]\s*|\s{2,}', text)
                    for part in parts:
                        for t in re.split(r'\s+', part.strip()):
                            t = t.strip()
                            if t and t.lower() not in ("tag", "tags"):
                                tags.append(t)

            # Pattern 5: plain text after "Tags:" label anywhere in HTML
            pattern5 = r'[Tt]ags?\s*:\s*([^<\n]+?)(?:\s*</[^>]*>|$)'
            matches5 = re.findall(pattern5, html, re.IGNORECASE | re.MULTILINE)
            for m in matches5:
                text = unescape(m).strip()
                if not text:
                    continue
                parts = re.split(r'\s*[-–—,;]\s*|\s{2,}', text)
                for part in parts:
                    for t in re.split(r'\s+', part.strip()):
                        t = t.strip()
                        if t and t.lower() not in ("tag", "tags"):
                            tags.append(t)

            utils.log_debug(
                f"BandcampSource: regex/JSON fallback extracted_raw={len(tags)} tags (sample={tags[:8]!r})"
            )

            # Dedupe and normalize
            clean: List[str] = []
            seen = set()
            for t in tags:
                norm = utils.normalize_tag(t)
                if norm and norm not in seen:
                    seen.add(norm)
                    clean.append(t.strip())

            utils.log_debug(
                f"BandcampSource: regex/JSON fallback normalized {len(clean)} unique tags (sample={clean[:8]!r})"
            )

            return clean
    
    def _fallback_search(self, album, artist: str, album_title: str, callback):
        """
        Search Bandcamp by artist + album title.
        
        Improved to work with minimal metadata (just artist + title).
        Reuses logic from bandcamp_tag_fetcher if available, otherwise implements basic search.
        """
        try:
            # Try to import from bandcamp_tag_fetcher if available
            from bandcamp_tag_fetcher.client import search_bandcamp_album
            
            def _search_callback(url, error):
                if error:
                    utils.log_debug(f"BandcampSource: Fallback search failed: {error}")
                    callback(self.name, None, f"fallback_search_failed: {error}")
                    return
                
                if not url:
                    utils.log_debug("BandcampSource: Fallback search found no results")
                    callback(self.name, None, "fallback_no_results")
                    return
                
                # Use the found URL to fetch tags
                utils.log_info(f"UMA: bandcamp: using URL {url} (found via fallback search)")
                self.fetch_from_url(album, url, callback)
            
            search_bandcamp_album(album, artist, album_title, _search_callback)
        except ImportError:
            # Fallback: implement basic search ourselves
            utils.log_debug("BandcampSource: bandcamp_tag_fetcher not available, using built-in fallback search")
            self._builtin_fallback_search(album, artist, album_title, callback)
    
    def _builtin_fallback_search(self, album, artist: str, album_title: str, callback):
        """
        Built-in Bandcamp search implementation.
        Used when bandcamp_tag_fetcher is not available.
        """
        # Build search query
        query = f"{artist} {album_title}".strip()
        query_encoded = urllib.parse.quote_plus(query)
        search_url = f"https://bandcamp.com/search?q={query_encoded}&item_type=a"
        
        utils.log_debug(f"BandcampSource: fallback search URL: {search_url}")
        
        # Increment requests counter
        album._requests += 1
        
        # Get network manager
        manager = getattr(album.tagger, '_network_manager', None)
        if not manager:
            manager = QNetworkAccessManager()
            album.tagger._network_manager = manager
            
        qurl = QUrl(search_url)
        request = QNetworkRequest(qurl)
        request.setRawHeader(b'User-Agent', b'MusicBrainz Picard UMA Plugin')
        
        reply = manager.get(request)
        
        # Keep alive
        if not hasattr(album, '_uma_replies'):
            album._uma_replies = []
        album._uma_replies.append(reply)
        
        def _finished():
            try:
                if reply in album._uma_replies:
                    album._uma_replies.remove(reply)
                
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    error_msg = reply.errorString()
                    utils.log_warning(f"BandcampSource: Fallback search network error: {error_msg}")
                    callback(self.name, None, f"fallback_search_network_error: {error_msg}")
                    return
                
                data = reply.readAll()
                html = bytes(data).decode('utf-8', errors='ignore')
                
                # Parse search results - look for album URLs
                # Simple regex-based parsing
                album_url_pattern = r'href=["\'](https?://[^"\']*bandcamp\.com/album/[^"\']*)["\']'
                matches = re.findall(album_url_pattern, html, re.IGNORECASE)
                
                if matches:
                    # Take first match (could be improved with scoring)
                    url = matches[0]
                    utils.log_info(f"UMA: bandcamp: using URL {url} (found via fallback search)")
                    self.fetch_from_url(album, url, callback)
                else:
                    utils.log_debug("BandcampSource: Fallback search found no album URLs in results")
                    callback(self.name, None, "fallback_no_results")
                
            except Exception as e:
                error_msg = f"parse error: {e}"
                utils.log_warning(f"BandcampSource: Fallback search failed: {error_msg}")
                utils.log_error(f"Bandcamp fallback search error: {e}", exc_info=True)
                callback(self.name, None, f"fallback_search_error: {error_msg}")
            finally:
                reply.deleteLater()
                album._requests -= 1
                album._finalize_loading(None)
                
        reply.finished.connect(_finished)


# ---------------------------------------------------------------------------
# Discogs Logic (Ported/Adapted)
# ---------------------------------------------------------------------------

def _extract_discogs_url(release, release_group=None):
    """
    Extract Discogs URL from MusicBrainz release and release-group relations.
    Looks for:
    1. Relations with type="discogs" (can be at release or release-group level)
    2. URL relations containing discogs.com/master/ or discogs.com/release/
    
    Checks in order:
    1. Release-level relations
    2. Release-group-level relations
    
    Returns:
        Tuple of (url_or_none, entity_type, scope) where:
        - entity_type is 'master', 'release', or None
        - scope is 'release', 'release-group', or None
    """
    release_relations_count = 0
    release_group_relations_count = 0
    discogs_type_count = 0
    
    # Step 1: Check release-level relations
    if release and isinstance(release, dict):
        relations = release.get("relations", [])
        if isinstance(relations, list):
            release_relations_count = len(relations)
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                
                # Check for type="discogs" relations
                relation_type = relation.get("type", "")
                if relation_type == "discogs":
                    discogs_type_count += 1
                    # Get URL from relation
                    url_obj = relation.get("url", {})
                    if isinstance(url_obj, dict):
                        resource = url_obj.get("resource", "")
                    else:
                        resource = relation.get("resource", "")
                    
                    if resource and "discogs.com" in resource.lower():
                        resource_lower = resource.lower()
                        if "/master/" in resource_lower:
                            utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release, relation_type=discogs)")
                            return resource, "master", "release"
                        elif "/release/" in resource_lower:
                            utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release, relation_type=discogs)")
                            return resource, "release", "release"
                
                # Also check URL relations (fallback for older MB data)
                url_obj = relation.get("url", {})
                if isinstance(url_obj, dict):
                    resource = url_obj.get("resource", "")
                else:
                    resource = relation.get("resource", "")
                
                if resource and "discogs.com" in resource.lower():
                    resource_lower = resource.lower()
                    if "/master/" in resource_lower:
                        utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release, relation_type=url)")
                        return resource, "master", "release"
                    elif "/release/" in resource_lower:
                        utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release, relation_type=url)")
                        return resource, "release", "release"
    
    # Step 2: Check release-group-level relations
    if release_group and isinstance(release_group, dict):
        relations = release_group.get("relations", [])
        if isinstance(relations, list):
            release_group_relations_count = len(relations)
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                
                # Check for type="discogs" relations
                relation_type = relation.get("type", "")
                if relation_type == "discogs":
                    discogs_type_count += 1
                    # Get URL from relation
                    url_obj = relation.get("url", {})
                    if isinstance(url_obj, dict):
                        resource = url_obj.get("resource", "")
                    else:
                        resource = relation.get("resource", "")
                    
                    if resource and "discogs.com" in resource.lower():
                        resource_lower = resource.lower()
                        if "/master/" in resource_lower:
                            utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release-group, relation_type=discogs)")
                            return resource, "master", "release-group"
                        elif "/release/" in resource_lower:
                            utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release-group, relation_type=discogs)")
                            return resource, "release", "release-group"
                
                # Also check URL relations (fallback for older MB data)
                url_obj = relation.get("url", {})
                if isinstance(url_obj, dict):
                    resource = url_obj.get("resource", "")
                else:
                    resource = relation.get("resource", "")
                
                if resource and "discogs.com" in resource.lower():
                    resource_lower = resource.lower()
                    if "/master/" in resource_lower:
                        utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release-group, relation_type=url)")
                        return resource, "master", "release-group"
                    elif "/release/" in resource_lower:
                        utils.log_info(f"DiscogsSource: selected Discogs URL={resource} (scope=release-group, relation_type=url)")
                        return resource, "release", "release-group"
    
    # Log diagnostic info before returning None
    utils.log_debug(f"DiscogsSource: no Discogs URL found - inspected {release_relations_count} release relations, {release_group_relations_count} release-group relations, {discogs_type_count} with type='discogs'")
    return None, None, None


def _parse_discogs_id(url: str, entity_type: str) -> Optional[int]:
    """
    Parse Discogs ID from URL.
    
    Examples:
        https://www.discogs.com/master/1571881-Bernache-Your-Name -> 1571881
        https://www.discogs.com/release/123456 -> 123456
        https://www.discogs.com/master/1571881?foo=bar -> 1571881
    """
    if not url or not entity_type:
        return None
    
    # Pattern: /master/123456 or /release/123456 (with optional trailing text)
    pattern = rf"/{entity_type}/(\d+)"
    match = re.search(pattern, url, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            pass
    
    return None


class DiscogsSource(SourceBase):
    name = "discogs"
    
    def fetch(self, album, callback):
        utils.log_debug("Discogs fetch requested")
        
        # 1. Check token
        token = _get_config("uma_discogs_token", "")
        if not token:
            utils.log_debug("No Discogs token configured")
            callback(self.name, None, "no_token")
            return
            
        # 2. Try to get Discogs URL from MB relations (both release and release-group)
        release = getattr(album, '_uma_release', None)
        if not release and hasattr(album, 'release'):
            release = album.release
        
        release_group = getattr(album, '_uma_release_group', None)
        if not release_group and hasattr(album, 'release_group'):
            release_group = album.release_group
        # Also try to get from release dict if available
        if not release_group and release and isinstance(release, dict):
            release_group = release.get("release-group")
        
        discogs_url, entity_type, scope = _extract_discogs_url(release, release_group)
        
        if discogs_url and entity_type:
            # Extract ID from URL
            discogs_id = _parse_discogs_id(discogs_url, entity_type)
            if discogs_id:
                utils.log_info(f"DiscogsSource: using URL={discogs_url} (type={entity_type}, id={discogs_id}, scope={scope})")
                self._fetch_by_id(album, entity_type, discogs_id, token, callback)
                return
            else:
                utils.log_warning(f"DiscogsSource: failed to parse ID from URL: {discogs_url}")
        else:
            utils.log_debug(f"DiscogsSource: no Discogs URL found in MB relations (url={discogs_url}, type={entity_type}, scope={scope})")
        
        # 3. Fallback: Get Search Query (Artist/Album)
        # We need metadata. `album` is picard.album.Album
        # album.metadata should be populated with MB data
        artist = album.metadata.get("albumartist") or album.metadata.get("artist")
        release_title = album.metadata.get("album")
        
        if not artist or not release_title:
             callback(self.name, None, "missing_metadata")
             return
             
        # 4. Search
        self._search_release(album, artist, release_title, token, callback)
    
    def _fetch_by_id(self, album, entity_type: str, entity_id: int, token: str, callback):
        """
        Fetch Discogs metadata by master or release ID.
        
        For masters:
        1. Call /masters/{id} to get master data
        2. If master has main_release, optionally fetch that release for more complete data
        3. Extract genres/styles from master (or release if fetched)
        
        For releases:
        1. Call /releases/{id} directly
        2. Extract genres/styles
        """
        if entity_type == "master":
            # Fetch master first
            url = f"https://api.discogs.com/masters/{entity_id}?token={token}"
            utils.log_debug(f"DiscogsSource: fetching master {entity_id} from {url}")
            
            def _master_handler(response, reply, error):
                if error:
                    utils.log_error(f"Discogs API Error (master {entity_id}): {error}")
                    callback(self.name, None, str(error))
                    return
                
                try:
                    # Log raw response structure for debugging
                    utils.log_debug(f"DiscogsSource: master {entity_id} API response keys: {list(response.keys()) if isinstance(response, dict) else 'not a dict'}")
                    
                    # Extract genres/styles from master
                    genres = response.get("genres", [])
                    styles = response.get("styles", [])
                    
                    # Log raw values before normalization
                    utils.log_debug(f"DiscogsSource: master {entity_id} raw genres={genres!r} (type={type(genres).__name__}), raw styles={styles!r} (type={type(styles).__name__})")
                    
                    # Normalize to lists
                    if not isinstance(genres, list):
                        genres = [genres] if genres else []
                    if not isinstance(styles, list):
                        styles = [styles] if styles else []
                    
                    # Log after normalization, before missing_metadata check
                    utils.log_info(f"DiscogsSource: parsed Discogs genres={genres!r}, styles={styles!r} (entity_type=master, id={entity_id})")
                    
                    # Always try to fetch main_release for more complete data (releases often have more detailed genres/styles)
                    main_release_id = response.get("main_release")
                    if main_release_id and isinstance(main_release_id, int):
                        utils.log_info(f"DiscogsSource: resolved master {entity_id} to main_release {main_release_id} (master has genres={bool(genres)}, styles={bool(styles)})")
                        # Fetch main release to get potentially more complete genres/styles
                        self._fetch_release_by_id(album, main_release_id, token, genres, styles, callback)
                        return
                    
                    # If no main_release, try first release from releases list if master has no genres/styles
                    if not genres and not styles:
                        releases = response.get("releases", [])
                        if isinstance(releases, list) and len(releases) > 0:
                            # Try first release
                            first_release = releases[0]
                            if isinstance(first_release, dict):
                                first_release_id = first_release.get("id")
                                if first_release_id and isinstance(first_release_id, int):
                                    utils.log_info(f"DiscogsSource: master {entity_id} has no genres/styles, trying first release {first_release_id} from releases list")
                                    self._fetch_release_by_id(album, first_release_id, token, genres, styles, callback)
                                    return
                        
                        utils.log_warning(f"DiscogsSource: returning missing_metadata because master {entity_id} has no genres or styles (genres={genres!r}, styles={styles!r}) and no usable releases")
                        callback(self.name, None, "missing_metadata")
                        return
                    
                    # Use master data directly (has at least some genres or styles, and no main_release)
                    utils.log_info(f"DiscogsSource: using master {entity_id} data directly (no main_release found)")
                    # Build tags from styles and genres so the mapping engine can
                    # apply Tag → Genre rules (e.g. map Discogs styles to UMA genres).
                    # Order: styles first (more specific), then genres, with de-dupe.
                    tags = []
                    seen_tags = set()
                    for v in styles + genres:
                        if not v:
                            continue
                        key = v.strip().lower()
                        if key and key not in seen_tags:
                            seen_tags.add(key)
                            tags.append(v)

                    block = SourceBlock(
                        name=self.name,
                        genres=genres,
                        styles=styles,
                        tags=tags
                    )
                    
                    utils.log_info(f"DiscogsSource: returning metadata for master {entity_id}: genres={genres!r}, styles={styles!r}")
                    callback(self.name, block, None)
                    
                except Exception as e:
                    utils.log_error(f"Discogs parse error (master {entity_id}): {e}", exc_info=True)
                    utils.log_error(f"DiscogsSource: exception details - response type={type(response).__name__}, response={str(response)[:200]}")
                    callback(self.name, None, str(e))
            
            utils.get_api().web_service.get_url(
                url=url,
                handler=_master_handler,
                parse_response_type="json",
                priority=True
            )
            
        elif entity_type == "release":
            self._fetch_release_by_id(album, entity_id, token, [], [], callback)
        else:
            callback(self.name, None, f"unknown_entity_type: {entity_type}")
    
    def _fetch_release_by_id(self, album, release_id: int, token: str, 
                             existing_genres: List[str], existing_styles: List[str], callback):
        """
        Fetch Discogs release by ID and merge with existing genres/styles (from master if applicable).
        """
        url = f"https://api.discogs.com/releases/{release_id}?token={token}"
        utils.log_debug(f"DiscogsSource: fetching release {release_id} from {url}")
        
        def _release_handler(response, reply, error):
            if error:
                utils.log_error(f"Discogs API Error (release {release_id}): {error}")
                # If we have existing data from master, use that
                if existing_genres or existing_styles:
                    utils.log_info(f"DiscogsSource: release {release_id} fetch failed, using master data: genres={existing_genres!r}, styles={existing_styles!r}")
                    block = SourceBlock(
                        name=self.name,
                        genres=existing_genres,
                        styles=existing_styles,
                        tags=[]
                    )
                    callback(self.name, block, None)
                else:
                    callback(self.name, None, str(error))
                return
            
            try:
                # Log raw response structure for debugging
                utils.log_debug(f"DiscogsSource: release {release_id} API response keys: {list(response.keys()) if isinstance(response, dict) else 'not a dict'}")
                
                # Extract genres/styles from release
                genres = response.get("genres", [])
                styles = response.get("styles", [])
                
                # Log raw values before normalization
                utils.log_debug(f"DiscogsSource: release {release_id} raw genres={genres!r} (type={type(genres).__name__}), raw styles={styles!r} (type={type(styles).__name__})")
                
                # Normalize to lists
                if not isinstance(genres, list):
                    genres = [genres] if genres else []
                if not isinstance(styles, list):
                    styles = [styles] if styles else []
                
                # Merge with existing (prefer release data over master data)
                final_genres = genres if genres else existing_genres
                final_styles = styles if styles else existing_styles
                
                # Log after normalization, before missing_metadata check
                utils.log_info(f"DiscogsSource: parsed Discogs genres={final_genres!r}, styles={final_styles!r} (entity_type=release, id={release_id}, from_master={bool(existing_genres or existing_styles)})")
                
                # Only return missing_metadata if BOTH genres and styles are empty
                if not final_genres and not final_styles:
                    utils.log_warning(f"DiscogsSource: returning missing_metadata because release {release_id} has no genres or styles (genres={final_genres!r}, styles={final_styles!r})")
                    callback(self.name, None, "missing_metadata")
                    return
                
                # Build tags from styles and genres so the mapping engine can
                # map Discogs styles/genres to UMA genres.
                tags = []
                seen_tags = set()
                for v in final_styles + final_genres:
                    if not v:
                        continue
                    key = v.strip().lower()
                    if key and key not in seen_tags:
                        seen_tags.add(key)
                        tags.append(v)

                block = SourceBlock(
                    name=self.name,
                    genres=final_genres,
                    styles=final_styles,
                    tags=tags
                )
                
                utils.log_info(f"DiscogsSource: returning metadata for release {release_id}: genres={final_genres!r}, styles={final_styles!r}")
                callback(self.name, block, None)
                
            except Exception as e:
                utils.log_error(f"Discogs parse error (release {release_id}): {e}", exc_info=True)
                # If we have existing data from master, use that
                if existing_genres or existing_styles:
                    # Build tags from existing styles/genres (from master)
                    tags = []
                    seen_tags = set()
                    for v in existing_styles + existing_genres:
                        if not v:
                            continue
                        key = v.strip().lower()
                        if key and key not in seen_tags:
                            seen_tags.add(key)
                            tags.append(v)

                    block = SourceBlock(
                        name=self.name,
                        genres=existing_genres,
                        styles=existing_styles,
                        tags=tags
                    )
                    callback(self.name, block, None)
                else:
                    callback(self.name, None, str(e))
        
        utils.get_api().web_service.get_url(
            url=url,
            handler=_release_handler,
            parse_response_type="json",
            priority=True
        )

    def _search_release(self, album, artist, title, token, callback):
        # Use Picard's webservice since it handles rate limiting nicely?
        # Or QNetworkAccessManager? existing plugin uses `album.tagger.webservice.get_url`
        
        params = {
            "token": token,
            "artist": artist,
            "release_title": title,
            "type": "release",
            "per_page": 5
        }
        query = urllib.parse.urlencode(params)
        url = f"https://api.discogs.com/database/search?{query}"
        
        utils.log_debug(f"Discogs Search: {url}")
        
        def _handler(response, reply, error):
            if error:
                utils.log_error(f"Discogs API Error: {error}")
                callback(self.name, None, str(error))
                return
            
            try:
                results = response.get("results", [])
                if not results:
                    utils.log_debug("Discogs: No results found")
                    callback(self.name, None, "no_results")
                    return
                
                # Simple pick first result for MVP (Refining logic to match existing plugin's scoring later if needed)
                # The existing plugin has complex scoring. For now, we take 0.
                best = results[0]
                
                genres = best.get("genre", [])
                styles = best.get("style", [])
                
                # Normalize to lists
                if not isinstance(genres, list):
                    genres = [genres] if genres else []
                if not isinstance(styles, list):
                    styles = [styles] if styles else []
                
                # Log before missing_metadata check
                utils.log_debug(f"DiscogsSource: url={url}, entity_type=search_result, genres={genres!r}, styles={styles!r}")
                
                # Only return missing_metadata if BOTH genres and styles are empty
                if not genres and not styles:
                    utils.log_debug("DiscogsSource: search result has no genres or styles")
                    callback(self.name, None, "missing_metadata")
                    return
                
                block = SourceBlock(
                    name=self.name,
                    genres=genres,
                    styles=styles,
                    tags=[]
                )
                
                callback(self.name, block, None)
                
            except Exception as e:
                utils.log_error(f"Discogs parse error: {e}", exc_info=True)
                callback(self.name, None, str(e))

        utils.get_api().web_service.get_url(
            url=url,
            handler=_handler,
            parse_response_type="json",
            priority=True
        )

# Factory / Repository
def get_source(name: str) -> SourceBase:
    if name == "bandcamp":
        return BandcampSource()
    if name == "discogs":
        return DiscogsSource()
    return None


# ---------------------------------------------------------------------------
# Test Helpers (for verification)
# ---------------------------------------------------------------------------

def _test_extract_bandcamp_urls_from_text():
    """
    Test helper to verify URL extraction from text.
    Can be called manually or in unit tests.
    """
    test_cases = [
        ("Check out https://bernache.bandcamp.com/releases for more music", 
         ["https://bernache.bandcamp.com/releases"]),
        ("Album available at https://artist.bandcamp.com/album/title and https://other.bandcamp.com/track/song",
         ["https://artist.bandcamp.com/album/title"]),  # Prefer album URLs
        ("No URLs here", []),
        ("Visit https://label.bandcamp.com/album/name for details.",
         ["https://label.bandcamp.com/album/name"]),
    ]
    
    for text, expected in test_cases:
        result = extract_bandcamp_urls_from_text(text)
        print(f"Test: '{text[:50]}...' -> {result}")
        assert result == expected or (expected and result and expected[0] in result), \
            f"Expected {expected}, got {result}"
    
    print("All URL extraction tests passed!")


def _test_resolve_bandcamp_url():
    """
    Test helper to verify multi-step URL resolution.
    """
    # Mock release with annotation
    release_with_annotation = {
        "relations": [],
        "annotation": "Available at https://bernache.bandcamp.com/releases"
    }
    
    # Mock release with relation
    release_with_relation = {
        "relations": [
            {
                "url": {
                    "resource": "https://artist.bandcamp.com/album/title"
                }
            }
        ]
    }
    
    class MockAlbum:
        pass
    
    album = MockAlbum()
    
    # Test annotation extraction
    url, source = resolve_bandcamp_url(album, release_with_annotation, None)
    assert url == "https://bernache.bandcamp.com/releases", f"Expected annotation URL, got {url}"
    assert source == "annotation", f"Expected 'annotation', got {source}"
    
    # Test relation extraction
    url, source = resolve_bandcamp_url(album, release_with_relation, None)
    assert url == "https://artist.bandcamp.com/album/title", f"Expected relation URL, got {url}"
    assert source == "release relations", f"Expected 'release relations', got {source}"
    
    print("All URL resolution tests passed!")


# Uncomment to run tests:
# if __name__ == "__main__":
#     _test_extract_bandcamp_urls_from_text()
#     _test_resolve_bandcamp_url()
