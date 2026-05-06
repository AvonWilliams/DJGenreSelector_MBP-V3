"""
Tag Mapping Engine for DJ Genre Selector.

Implements TAG → NORMALIZED_TAG → CLUSTER → GENRE pipeline.
Supports wildcard rules, first-match, single-winner, multi-winner modes.
"""

import re
from typing import List, Dict, Optional, Tuple, Set
from . import utils

def _get_config(key, default=None):
    return utils._get_config(key, default)


class GenreClusterMappingConfig:
    """
    Configuration structure for genre cluster mapping.
    
    Contains:
    - rules: dict mapping pattern -> cluster name (from x=y lines)
    - cluster_priority: ordered list of cluster names (from bare lines)
    """
    def __init__(self, rules: Dict[str, str] = None, cluster_priority: List[str] = None):
        self.rules = rules if rules is not None else {}
        self.cluster_priority = cluster_priority if cluster_priority is not None else []


class TagMappingEngine:
    """
    Core tag mapping engine that converts tags to genres using configurable rules.
    
    Extended syntax:
    - Lines with '=' define mapping rules: pattern = cluster
    - Bare lines (without '=') define cluster priority order
    - Comment lines (starting with #, //, or =) are ignored
    """
    
    def __init__(self):
        self.pairs: List[Tuple[str, str]] = []  # (pattern, replacement)
        self.use_regex: bool = False
        self.first_match_only: bool = False
        self.mode: str = "first_match"  # first_match, single_winner, multi_winner, override
        self.cluster_priority: List[str] = []  # Config-driven cluster priority list
        
    def refresh(self):
        """Refresh mapping pairs and cluster priority from configuration."""
        self.use_regex = _get_config("uma_mapping_use_regex", False)
        self.first_match_only = _get_config("uma_mapping_first_match_only", False)
        self.mode = _get_config("uma_mapping_mode", "first_match")
        
        mapping_text = _get_config("uma_tag_mapping", "")
        if not mapping_text:
            self.pairs = []
            self.cluster_priority = []
            utils.log_debug("MappingEngine: No mapping pairs defined.")
            return
        
        # Parse mapping file into rules and cluster priority
        config_data = self._parse_mapping_file(mapping_text)
        self.cluster_priority = config_data.cluster_priority
        
        def _make_re(map_string):
            """Convert wildcard pattern to regex."""
            re_string = str(map_string).strip().replace('.', '\n')
            re_string = re_string.replace('*', '.*').replace('?', '.')
            re_string = re_string.replace('^', '\\^').replace('$', '\\$')
            re_string = '^' + re_string.replace('\n', '\\.') + '$'
            return re_string
        
        self.pairs = []
        added_pairs_preview = []
        for pattern, replacement in config_data.rules.items():
            pattern_re = pattern if self.use_regex else _make_re(pattern)
            self.pairs.append((pattern_re, replacement))
            # Collect a short preview of added rules for logging
            if len(added_pairs_preview) < 10:
                added_pairs_preview.append(f'"{pattern}" → "{replacement}"')
        
        # Single summary log instead of one line per rule
        if self.pairs:
            preview = ", ".join(added_pairs_preview)
            total = len(self.pairs)
            if total > len(added_pairs_preview):
                preview += f", … (+{total - len(added_pairs_preview)} more)"
            utils.log_debug(f"MappingEngine: Loaded {total} mapping pairs: {preview}")
        else:
            utils.log_debug("MappingEngine: No valid mapping pairs after parsing.")
        
        if self.cluster_priority:
            utils.log_debug(f"MappingEngine: Loaded cluster priority ({len(self.cluster_priority)} items): {self.cluster_priority[:5]}{'...' if len(self.cluster_priority) > 5 else ''}")
        else:
            utils.log_debug("MappingEngine: No cluster priority defined, using default behavior")
    
    def _parse_mapping_file(self, mapping_text: str) -> GenreClusterMappingConfig:
        """
        Parse mapping file into rules and cluster priority.
        
        Parsing rules:
        1. Trim whitespace from each line
        2. Skip empty lines
        3. Skip comment lines (starting with #, //, or =)
        4. Lines with '=' are mapping rules: pattern = cluster
        5. Lines without '=' are cluster priority items
        
        Args:
            mapping_text: Raw mapping file content
            
        Returns:
            GenreClusterMappingConfig with rules and cluster_priority
        """
        rules: Dict[str, str] = {}
        cluster_priority: List[str] = []
        
        lines_split = re.compile(r"\r\n|\n\r|\n").split
        for line in lines_split(mapping_text):
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Skip comment lines (starting with #, //, or section headers like =====)
            # Section headers are lines that start with = and have multiple = characters
            if (line.startswith('#') or 
                line.startswith('//') or 
                (line.startswith('=') and line.count('=') >= 3)):  # Section header like "======"
                continue
            
            # Check if line contains '='
            if '=' in line:
                # Parse mapping rule: pattern = cluster
                parts = line.split('=', 1)
                if len(parts) == 2:
                    pattern = parts[0].strip()
                    cluster = parts[1].strip()
                    if pattern and cluster:
                        rules[pattern] = cluster
            else:
                # Bare line = cluster priority item
                cluster_name = line.strip()
                if cluster_name:
                    # Normalize: just strip, preserve case (clusters are Title Case)
                    cluster_priority.append(cluster_name)
        
        return GenreClusterMappingConfig(rules=rules, cluster_priority=cluster_priority)
    
    def get_mapping_keys(self) -> Set[str]:
        """
        Get normalized mapping keys (left side of rules) for whitelist filtering.
        
        Returns:
            Set of normalized (lowercase, trimmed) mapping keys.
        """
        if not self.pairs:
            self.refresh()
        
        keys = set()
        # Extract keys from rules (before regex conversion)
        mapping_text = _get_config("uma_tag_mapping", "")
        if mapping_text:
            config_data = self._parse_mapping_file(mapping_text)
            for pattern in config_data.rules.keys():
                normalized = utils.normalize_tag(pattern)
                if normalized:
                    keys.add(normalized)
        
        return keys
    
    def get_cluster_priority(self) -> List[str]:
        """
        Get cluster priority list from config.
        
        Returns:
            Ordered list of cluster names, or empty list if not configured
        """
        if not self.cluster_priority:
            self.refresh()
        return self.cluster_priority.copy()
    
    def map_tags(self, tags: List[str]) -> List[str]:
        """
        Map tags to genres using configured rules.
        
        Returns:
            List of mapped genre strings (may be empty, single, or multiple depending on mode).
        """
        if not self.pairs:
            self.refresh()
        
        if not self.pairs or not tags:
            return []
        
        matched_genres: List[str] = []
        seen_patterns: Set[int] = set()
        
        for tag in tags:
            tag_str = str(tag).strip()
            if not tag_str:
                continue
            
            # Check each mapping pattern
            for idx, (pattern, replacement) in enumerate(self.pairs):
                try:
                    if re.search(pattern, tag_str, re.IGNORECASE):
                        if self.mode == "first_match" and matched_genres:
                            # First match mode: return first match only
                            return [matched_genres[0]] if matched_genres else [replacement]
                        
                        if idx not in seen_patterns:
                            matched_genres.append(replacement)
                            seen_patterns.add(idx)
                            utils.log_debug(f"MappingEngine: Matched '{tag_str}' → '{replacement}' via pattern '{pattern}'")
                            
                            if self.first_match_only:
                                return [replacement]
                            
                            if self.mode == "single_winner":
                                # Single winner: return first match
                                return [replacement]
                except re.error as e:
                    utils.log_warning(f"MappingEngine: Invalid regex pattern '{pattern}': {e}")
        
        # Multi-winner mode returns all matches, others return first
        if self.mode == "multi_winner":
            return matched_genres
        elif matched_genres:
            return [matched_genres[0]]
        
        return []
    
    def compute_candidate_clusters(self, tags: List[str]) -> Dict[str, List[str]]:
        """
        Map tags to candidate clusters, returning a mapping of cluster_name -> [source_tags].
        
        This method collects ALL possible cluster mappings from tags, regardless of mode settings,
        to support soft cluster selection in the Collector.
        
        Args:
            tags: List of tag strings to map
            
        Returns:
            Dictionary mapping cluster_name -> list of source tags that mapped to it
        """
        if not self.pairs:
            self.refresh()
        
        if not self.pairs or not tags:
            return {}
        
        # Map cluster -> list of source tags
        cluster_to_tags: Dict[str, List[str]] = {}
        
        for tag in tags:
            tag_str = str(tag).strip()
            if not tag_str:
                continue
            
            # Check each mapping pattern (collect ALL matches, not just first)
            for pattern, replacement in self.pairs:
                try:
                    if re.search(pattern, tag_str, re.IGNORECASE):
                        cluster = replacement.strip()
                        if cluster:
                            if cluster not in cluster_to_tags:
                                cluster_to_tags[cluster] = []
                            if tag_str not in cluster_to_tags[cluster]:
                                cluster_to_tags[cluster].append(tag_str)
                except re.error as e:
                    utils.log_warning(f"MappingEngine: Invalid regex pattern '{pattern}': {e}")
        
        return cluster_to_tags


class GenericGenreSuppressor:
    """
    Suppresses generic genres from the merged genre list.
    
    Behavior:
    - Reads suppression patterns from config (one per line).
    - Matching behavior depends on global "Use regex patterns" setting:
      * If OFF: treats each pattern as Picard-style wildcard (* matches any substring, case-insensitive).
      * If ON: treats each pattern as full regex (case-insensitive).
    - Applied to normalized merged genres (after all sources are merged, before writing to Picard).
    - Only suppresses when clusters are present (to avoid removing all genres when no specific mapping exists).
    
    Examples (wildcards off):
    - "Electronic" → removes exact match "Electronic" (case-insensitive)
    - "*electronic*" → removes "Electronic", "Electronic Music", "Electronics Something"
    - "*germany*" → removes "Germany"
    
    Examples (regex on):
    - "(?i)electronic" → removes any genre containing "electronic" (case-insensitive)
    - "^Electronic$" → removes exact match "Electronic"
    """
    
    def __init__(self):
        self.generic_patterns: List[str] = []  # Raw patterns from config
        self.compiled_patterns: List[re.Pattern] = []  # Compiled regex patterns
        self.use_regex: bool = False
    
    def refresh(self):
        """
        Refresh generic genre patterns from configuration.
        
        Reads the suppression list and compiles patterns based on the global
        "Use regex patterns" setting. Patterns are stored as-is for wildcard mode,
        or compiled as regex for regex mode.
        """
        generic_text = _get_config("uma_generic_genres", 
            "Electronic\nElectronica\nElectronic Music\nRock\nPop")
        self.use_regex = _get_config("uma_mapping_use_regex", False)
        self.generic_patterns = []
        self.compiled_patterns = []
        
        if generic_text:
            for line in generic_text.splitlines():
                pattern = line.strip()
                if pattern:
                    self.generic_patterns.append(pattern)
                    # Compile pattern based on mode
                    if self.use_regex:
                        # Treat as full regex (case-insensitive)
                        try:
                            compiled = re.compile(pattern, re.IGNORECASE)
                            self.compiled_patterns.append(compiled)
                        except re.error as e:
                            utils.log_warning(f"GenericSuppressor: Invalid regex pattern '{pattern}': {e}")
                            # Fall back to literal match for invalid regex
                            self.compiled_patterns.append(None)
                    else:
                        # Convert wildcard to regex (Picard-style: * matches any substring)
                        # Escape special regex chars except *
                        escaped = re.escape(pattern)
                        # Replace escaped \* with .* (match any substring)
                        wildcard_regex = escaped.replace(r'\*', '.*')
                        # Make it match anywhere in the string (not just start/end)
                        wildcard_regex = f".*{wildcard_regex}.*"
                        try:
                            compiled = re.compile(wildcard_regex, re.IGNORECASE)
                            self.compiled_patterns.append(compiled)
                        except re.error as e:
                            utils.log_warning(f"GenericSuppressor: Error compiling wildcard pattern '{pattern}': {e}")
                            self.compiled_patterns.append(None)
        
        utils.log_debug(f"GenericSuppressor: Loaded {len(self.generic_patterns)} generic genre patterns (regex_mode={self.use_regex})")
    
    def is_generic(self, genre: str) -> bool:
        """
        Check if a genre matches any suppression pattern.
        
        Matching behavior:
        - If regex mode OFF: patterns are treated as wildcards (* matches any substring, case-insensitive).
        - If regex mode ON: patterns are treated as full regex (case-insensitive).
        - Empty patterns are ignored.
        
        Args:
            genre: Genre string to check
            
        Returns:
            True if genre matches any suppression pattern, False otherwise
        """
        if not self.generic_patterns:
            self.refresh()
        
        if not genre or not self.generic_patterns:
            return False
        
        genre_str = str(genre).strip()
        if not genre_str:
            return False
        
        # Check against all patterns
        for idx, pattern in enumerate(self.generic_patterns):
            compiled = self.compiled_patterns[idx] if idx < len(self.compiled_patterns) else None
            
            if compiled is None:
                # Invalid pattern, skip
                continue
            
            # For wildcard mode: if pattern has no *, treat as exact match only
            # For regex mode: use the compiled regex as-is
            if self.use_regex:
                # Full regex match (case-insensitive already in compiled pattern)
                if compiled.search(genre_str):
                    return True
            else:
                # Wildcard mode: if pattern is literal (no *), do exact match comparison only
                # Otherwise use the compiled wildcard regex
                if '*' not in pattern:
                    # Exact match only (case-insensitive) - don't use compiled regex for literals
                    if genre_str.lower() == pattern.lower():
                        return True
                else:
                    # Wildcard pattern: use compiled regex (matches anywhere in string)
                    if compiled.search(genre_str):
                        return True
        
        return False
    
    def suppress_generics(self, genres: List[str], clusters: List[str]) -> List[str]:
        """
        Remove generic genres from the merged genre list.
        
        Suppression rules:
        - Suppresses generic genres when there are non-generic genres present (clusters).
        - If all genres are generic, keeps them all (fallback to prevent empty list).
        - Uses pattern matching (wildcard or regex) based on global "Use regex patterns" setting.
        - Case-insensitive matching.
        - Preserves order of non-suppressed genres.
        
        Args:
            genres: List of merged genre strings (from all sources)
            clusters: List of non-generic genres/clusters (from tag mapping or sources)
        
        Returns:
            Filtered list of genres with generics removed (if non-generic genres exist).
        """
        if not genres:
            return []
        
        # Check if we have any non-generic genres in the merged list itself
        # This handles cases where sources provide specific genres directly
        has_non_generic_in_list = False
        non_generic_genres = []
        for genre in genres:
            is_genre_generic = self.is_generic(genre)
            if not is_genre_generic:
                has_non_generic_in_list = True
                non_generic_genres.append(genre)
        
        # Suppress if we have clusters OR if the merged list itself contains non-generic genres
        should_suppress = bool(clusters) or has_non_generic_in_list
        
        utils.log_debug(f"GenericSuppressor: genres={genres!r}, clusters={clusters!r}, has_non_generic_in_list={has_non_generic_in_list}, non_generic_genres={non_generic_genres!r}, should_suppress={should_suppress}")
        
        if not should_suppress:
            # No clusters and all genres are generic - keep all (fallback)
            utils.log_debug(f"GenericSuppressor: No clusters and all genres are generic - keeping all genres")
            return genres
        
        # Remove generic genres
        filtered = []
        for genre in genres:
            is_genre_generic = self.is_generic(genre)
            if not is_genre_generic:
                filtered.append(genre)
            else:
                utils.log_debug(f"GenericSuppressor: Suppressed generic genre '{genre}' (clusters={bool(clusters)}, non-generic_genres={non_generic_genres!r})")
        
        # Keep at least one genre if all were generic (fallback to prevent empty list)
        # This should rarely happen now since we check has_non_generic_in_list above
        result = filtered if filtered else genres
        utils.log_debug(f"GenericSuppressor: Suppression result: {result!r} (filtered={len(filtered)}, original={len(genres)})")
        return result
