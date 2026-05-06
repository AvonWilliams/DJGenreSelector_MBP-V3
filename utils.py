import unicodedata

PLUGIN_NAME = "DJ Genre Selector"
LOG_PREFIX = "UMA"

_api = None


def set_api(api):
    global _api
    _api = api


def get_api():
    return _api


def _get_config(key, default=None):
    """Safely get a config value with default."""
    if _api is None:
        return default
    try:
        return _api.global_config.setting[key]
    except (KeyError, TypeError, AttributeError):
        return default


def _get_debug_enabled():
    """Check if debug logging is enabled."""
    return _get_config("uma_debug", False)


def log_debug(message):
    """Log debug message if debug logging is enabled."""
    if _get_debug_enabled() and _api is not None:
        _api.logger.debug(f"{LOG_PREFIX}: {message}")


def log_info(message):
    """Log info message."""
    if _api is not None:
        _api.logger.info(f"{LOG_PREFIX}: {message}")


def log_warning(message):
    """Log warning message."""
    if _api is not None:
        _api.logger.warning(f"{LOG_PREFIX}: {message}")


def log_error(message, exc_info=False):
    """Log error message."""
    if _api is not None:
        _api.logger.error(f"{LOG_PREFIX}: {message}")


def normalize_tag(tag):
    """
    Normalize a tag string for comparison.
    - Lowercase
    - Strip whitespace
    - Unicode normalization (NFKD)
    """
    if not tag:
        return ""

    # Unicode normalization
    tag = unicodedata.normalize('NFKD', str(tag))

    # Lowercase and strip
    tag = tag.strip().lower()

    return tag


def migrate_legacy_config():
    """
    Migrate legacy BandcampTF config to UMA config.
    Called once on plugin load if legacy config exists.
    """
    if _api is None:
        return
    try:
        setting = _api.global_config.setting

        # Check if migration needed
        if _get_config("uma_config_migrated", False):
            return  # Already migrated

        migrated_any = False

        # Migrate mapping table
        legacy_mapping = _get_config("bandcamp_tag_mapping", "")
        if legacy_mapping and not _get_config("uma_tag_mapping"):
            setting["uma_tag_mapping"] = legacy_mapping
            log_info("Migrated bandcamp_tag_mapping → uma_tag_mapping")
            migrated_any = True

        # Migrate generic genres
        legacy_generic = _get_config("bandcamp_generic_genres", "")
        if legacy_generic and not _get_config("uma_generic_genres"):
            setting["uma_generic_genres"] = legacy_generic
            log_info("Migrated bandcamp_generic_genres → uma_generic_genres")
            migrated_any = True

        # Migrate fallback search
        legacy_fallback = _get_config("bandcamp_fallback_search", False)
        if legacy_fallback and not _get_config("uma_bandcamp_fallback_search"):
            setting["uma_bandcamp_fallback_search"] = legacy_fallback
            log_info("Migrated bandcamp_fallback_search → uma_bandcamp_fallback_search")
            migrated_any = True

        # Migrate mapping options
        legacy_use_regex = _get_config("bandcamp_tag_mapper_use_regex", False)
        if legacy_use_regex and not _get_config("uma_mapping_use_regex"):
            setting["uma_mapping_use_regex"] = legacy_use_regex
            migrated_any = True

        legacy_first_match = _get_config("bandcamp_tag_mapper_first_match_only", False)
        if legacy_first_match and not _get_config("uma_mapping_first_match_only"):
            setting["uma_mapping_first_match_only"] = legacy_first_match
            migrated_any = True

        # Mark as migrated
        if migrated_any:
            setting["uma_config_migrated"] = True
            log_info("UMA: Legacy config migration completed")

    except Exception as e:
        log_error(f"Migration error: {e}")
