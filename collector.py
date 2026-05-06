from typing import List, Dict, Any, Optional, Set
import re
from . import utils
from .mapping_engine import TagMappingEngine, GenericGenreSuppressor

def _get_config(key, default=None):
    return utils._get_config(key, default)

class SourceBlock:
    """Represents metadata collected from a single source."""
    def __init__(self, name: str, genres: List[str] = None, styles: List[str] = None, 
                 tags: List[str] = None, comments: List[str] = None, extra: Dict[str, Any] = None):
        self.name = name
        self.genres = genres if genres is not None else []
        self.styles = styles if styles is not None else []
        self.tags = tags if tags is not None else []
        self.comments = comments if comments is not None else []
        self.extra = extra if extra is not None else {}

class AlbumCollector:
    """
    Orchestrates metadata collection and merging for a single album.
    Implements: collect → normalize → map → merge → suppress → write pipeline.
    """
    def __init__(self, album_id: str, album_title: str = None):
        self.album_id = album_id
        self.album_title = album_title or 'unknown'
        self.sources: Dict[str, SourceBlock] = {}
        
        # Source state tracking
        self.pending_sources: Set[str] = set()
        self.completed_sources: Set[str] = set()
        self.failed_sources: Set[str] = set()
        self.finalized: bool = False
        
        # Pipeline components
        self.mapping_engine = TagMappingEngine()
        self.generic_suppressor = GenericGenreSuppressor()
        
        # Merged results
        self.merged_genres: List[str] = []
        self.merged_styles: List[str] = []
        self.merged_tags: List[str] = []
        self.merged_comment: str = ""
        
        self.applied: bool = False
        self._normalized_cache: Dict[str, List[str]] = {}  # Cache normalized tags per source
        self._album_object = None  # Store album reference for finalization

    def set_album_title(self, album_title: str):
        """Update album title for logging."""
        if album_title and album_title != 'unknown':
            self.album_title = album_title
    
    def initialize_sources(self, source_names: Set[str]):
        """Initialize pending sources list."""
        self.pending_sources = set(source_names)
        utils.log_debug(f"Collector: Initialized pending sources for album '{self.album_title}': {self.pending_sources}")
    
    def _all_sources_resolved(self) -> bool:
        """Check if all pending sources have completed (success or failure)."""
        return not self.pending_sources
    
    def _mark_source_done(self, source_name: str, success: bool):
        """Mark a source as completed or failed."""
        if source_name in self.pending_sources:
            self.pending_sources.remove(source_name)
            if success:
                self.completed_sources.add(source_name)
                utils.log_debug(f"Collector: Source '{source_name}' completed for album '{self.album_title}'")
            else:
                self.failed_sources.add(source_name)
                utils.log_debug(f"Collector: Source '{source_name}' failed for album '{self.album_title}'")
        else:
            utils.log_debug(f"Collector: Warning - source '{source_name}' not in pending list for album '{self.album_title}'")
    
    def add_source_data(self, source_name: str, data: SourceBlock, album_title=None):
        """Add data from a source."""
        self.sources[source_name] = data
        
        # Update album title if provided
        if album_title and album_title != 'unknown':
            self.set_album_title(album_title)
        
        # Log summary of raw metadata received from source
        # (Detailed raw tags are already logged in sources.py after extraction)
        utils.log_debug(f"Collector: Received data from '{source_name}' for album '{self.album_title}': tags={len(data.tags)}, genres={len(data.genres)}, styles={len(data.styles)}")

    def normalize_source(self, source_name: str) -> Optional[SourceBlock]:
        """
        Normalize tags from a source:
        - Lowercasing
        - Trimming
        - Deduping
        - Unicode fixes
        """
        if source_name not in self.sources:
            return None
        
        block = self.sources[source_name]
        
        # Normalize tags
        normalized_tags = []
        seen = set()
        for tag in block.tags:
            norm = utils.normalize_tag(tag)
            if norm and norm not in seen:
                seen.add(norm)
                normalized_tags.append(tag.strip())  # Keep original capitalization
        
        # Normalize genres and styles similarly
        normalized_genres = []
        seen_genres = set()
        for genre in block.genres:
            norm = utils.normalize_tag(genre)
            if norm and norm not in seen_genres:
                seen_genres.add(norm)
                normalized_genres.append(genre.strip())
        
        normalized_styles = []
        seen_styles = set()
        for style in block.styles:
            norm = utils.normalize_tag(style)
            if norm and norm not in seen_styles:
                seen_styles.add(norm)
                normalized_styles.append(style.strip())
        
        normalized_block = SourceBlock(
            name=block.name,
            genres=normalized_genres,
            styles=normalized_styles,
            tags=normalized_tags,
            comments=block.comments,
            extra=block.extra
        )
        
        self._normalized_cache[source_name] = normalized_tags
        utils.log_debug(f"Collector: Normalized {source_name}: {len(normalized_tags)} tags, {len(normalized_genres)} genres")
        
        return normalized_block

    def map_tags_to_genres(self, tags: List[str]) -> List[str]:
        """Apply tag mapping engine to convert tags to genres."""
        if not tags:
            utils.log_info(f"Tag mapping result for album '{self.album_title}': no tags to map")
            return []
        
        self.mapping_engine.refresh()
        mapped = self.mapping_engine.map_tags(tags)
        
        if mapped:
            utils.log_info(f"Tag mapping result for album '{self.album_title}': genre={mapped}, style=[], extra=[]")
        else:
            utils.log_info(f"Tag mapping produced no genre/style for album '{self.album_title}'")
        
        utils.log_debug(f"Collector: Mapped {len(tags)} tags → {len(mapped)} genres: {mapped}")
        return mapped

    def merge(self):
        """
        Merge data from all sources based on configuration rules.
        Pipeline: normalize → collect all tags → compute candidate clusters → soft cluster selection → filter styles/genres
        
        Algorithm summary:
        - We prefer the most specific DJ-useful cluster (Prog-Psy, Psybient, Psychill, PsyDub, ...)
          and use Electronica only when nothing more specific can be inferred.
        """
        utils.log_debug(f"Collector: Starting merge pipeline for album '{self.album_title}'")
        
        # Step 1: Normalize all sources
        normalized_sources = {}
        all_tags = []
        
        for source_name in self.sources.keys():
            norm_block = self.normalize_source(source_name)
            if norm_block:
                normalized_sources[source_name] = norm_block
                all_tags.extend(norm_block.tags)
        
        utils.log_debug(f"Collector: Normalized {len(normalized_sources)} sources, collected {len(all_tags)} tags")
        
        # Step 2: Collect ALL tags from all sources (genres + styles + free tags)
        # This unified tag pool will be used for cluster mapping
        all_normalized_tags = []
        seen_tag_norms = set()
        
        for source_name in normalized_sources.keys():
            block = normalized_sources[source_name]
            # Add genres
            for genre in block.genres:
                norm = utils.normalize_tag(genre)
                if norm and norm not in seen_tag_norms:
                    seen_tag_norms.add(norm)
                    all_normalized_tags.append(genre)
            # Add styles
            for style in block.styles:
                norm = utils.normalize_tag(style)
                if norm and norm not in seen_tag_norms:
                    seen_tag_norms.add(norm)
                    all_normalized_tags.append(style)
            # Add free tags
            for tag in block.tags:
                norm = utils.normalize_tag(tag)
                if norm and norm not in seen_tag_norms:
                    seen_tag_norms.add(norm)
                    all_normalized_tags.append(tag)
        
        utils.log_debug(f"Collector: Collected {len(all_normalized_tags)} normalized tags from all sources (genres+styles+tags)")
        
        # Step 3: Compute candidate clusters from all tags
        # This returns a mapping: cluster_name -> [source_tags that mapped to it]
        self.mapping_engine.refresh()
        candidate_cluster_map = self.mapping_engine.compute_candidate_clusters(all_normalized_tags)
        
        # Also check if any genres/styles are already cluster names (direct matches)
        # This handles cases where sources provide cluster names directly (e.g., "Psybient" as a genre)
        # Extract cluster names from mapping configuration (rules + priority list)
        cluster_names = set()
        
        # Get clusters from mapping rules (right side of rules) and priority list
        mapping_text = _get_config("uma_tag_mapping", "")
        if mapping_text:
            config_data = self.mapping_engine._parse_mapping_file(mapping_text)
            # Add clusters from rules (right side of mapping rules)
            for cluster in config_data.rules.values():
                if cluster:
                    cluster_names.add(cluster.strip())
            # Add clusters from priority list
            for cluster in config_data.cluster_priority:
                if cluster:
                    cluster_names.add(cluster.strip())
        
        if cluster_names:
            utils.log_debug(f"Collector: Using {len(cluster_names)} cluster names from mapping config for direct matching")
            for source_name in normalized_sources.keys():
                block = normalized_sources[source_name]
                # Check genres
                for genre in block.genres:
                    genre_clean = genre.strip()
                    if genre_clean in cluster_names:
                        if genre_clean not in candidate_cluster_map:
                            candidate_cluster_map[genre_clean] = []
                        if genre not in candidate_cluster_map[genre_clean]:
                            candidate_cluster_map[genre_clean].append(genre)
                # Check styles
                for style in block.styles:
                    style_clean = style.strip()
                    if style_clean in cluster_names:
                        if style_clean not in candidate_cluster_map:
                            candidate_cluster_map[style_clean] = []
                        if style not in candidate_cluster_map[style_clean]:
                            candidate_cluster_map[style_clean].append(style)
        
        if candidate_cluster_map:
            utils.log_debug(f"Collector: Candidate clusters: {dict((k, v[:3]) for k, v in candidate_cluster_map.items())}")
        
        # Step 4: Select final genre cluster using soft selection algorithm
        candidate_clusters = set(candidate_cluster_map.keys())
        final_cluster = self._select_final_cluster(candidate_clusters, candidate_cluster_map)
        
        if final_cluster:
            self.merged_genres = [final_cluster]
            utils.log_debug(f"Collector: Selected final genre cluster: '{final_cluster}' from candidates {sorted(candidate_clusters)}")
        else:
            # Fallback: use original Discogs genre if available, or leave empty
            genre_priority = self._get_priority_list("uma_priority_genre", ["bandcamp", "discogs"])
            fallback_genre = None
            for source_name in genre_priority:
                if source_name in normalized_sources:
                    block = normalized_sources[source_name]
                    if block.genres:
                        fallback_genre = block.genres[0]
                        break
            
            if fallback_genre:
                self.merged_genres = [fallback_genre]
                utils.log_debug(f"Collector: No cluster mapped, using fallback genre: '{fallback_genre}'")
            else:
                self.merged_genres = []
                utils.log_debug(f"Collector: No cluster mapped and no fallback genre available")
        
        # Step 5: Merge styles from sources
        genre_priority = self._get_priority_list("uma_priority_genre", ["bandcamp", "discogs"])
        style_priority = self._get_priority_list("uma_priority_style", ["discogs", "bandcamp"])
        self.merged_styles = self._merge_field("styles", style_priority, normalized_sources)

        # Step 6: Generic-term filtering using ONE shared list with different behavior per field.
        # Shared list source: uma_generic_genres (one term per line).
        # - Styles: ALWAYS drop generic terms.
        # - Genres: already handled in soft cluster selection (Electronica only as fallback).
        generic_terms = self._get_generic_terms()
        if generic_terms:
            styles_before = self.merged_styles[:]
            self.merged_styles = self._filter_styles_with_generic_terms(self.merged_styles, generic_terms)
            if self.merged_styles != styles_before:
                utils.log_debug(f"Collector: Styles after generic-term filter: {self.merged_styles}")
        
        # Step 7: Merge tags for comments
        tags_pool = []
        seen_tags = set()
        for source_name in genre_priority:  # Use genre priority for tags too
            if source_name in normalized_sources:
                block = normalized_sources[source_name]
                for tag in block.tags:
                    norm = utils.normalize_tag(tag)
                    if norm and norm not in seen_tags:
                        seen_tags.add(norm)
                        tags_pool.append(tag)
        
        # Step 7.5: Filter tags using mapping keys whitelist (if enabled)
        filter_enabled = _get_config("uma_filter_tags_with_mapping", False)
        if filter_enabled:
            mapping_keys = self.mapping_engine.get_mapping_keys()
            if mapping_keys:
                filtered_tags = []
                dropped_tags = []
                for tag in tags_pool:
                    norm = utils.normalize_tag(tag)
                    if norm in mapping_keys:
                        filtered_tags.append(tag)
                    else:
                        dropped_tags.append(tag)
                
                # Log filtering results
                kept_count = len(filtered_tags)
                dropped_count = len(dropped_tags)
                kept_sample = filtered_tags[:10]
                dropped_sample = dropped_tags[:10]
                
                utils.log_debug(
                    f"Collector: Tag filtering (whitelist): {kept_count} kept / {dropped_count} dropped. "
                    f"kept={kept_sample!r}, dropped={dropped_sample!r}"
                )
                
                tags_pool = filtered_tags
            else:
                utils.log_debug("Collector: Tag filtering enabled but no mapping keys found - keeping all tags")
        
        # Step 7.6: Generic filter (B) - remove generic tags from comment hashtags
        # This applies AFTER whitelist filtering but BEFORE comment building
        # Cluster selection already happened (uses tags before this stage)
        generic_terms = self._get_generic_terms()
        if generic_terms:
            tags_before_generic = tags_pool[:]
            tags_pool = [tag for tag in tags_pool if not self._is_generic_term(tag, generic_terms)]
            if len(tags_pool) != len(tags_before_generic):
                dropped_generic = [t for t in tags_before_generic if t not in tags_pool]
                utils.log_debug(
                    f"Collector: Generic filter (B) for comment: dropped {len(dropped_generic)} generic tags: {dropped_generic[:5]}"
                )
        
        self.merged_tags = tags_pool

        # Step 8: Build comment (soft-normalized, deterministic)
        # Soft normalization rules for tags -> comment tokens:
        # - trim
        # - lowercase
        # - collapse separators (space|underscore|hyphen) → hyphen
        # - collapse multiple separators into one
        # - remove non [a-z0-9-+] chars
        # Then:
        # - dedupe
        # - sort alphanumerically
        # - prefix each token with '#'
        # - join with spaces
        comment_tokens = self._soft_normalize_comment_tokens(tags_pool)
        self.merged_comment = " ".join([f"#{t}" for t in comment_tokens])
        
        utils.log_debug(f"Collector: Merge complete - Genres={self.merged_genres}, Styles={self.merged_styles}, Tags={len(self.merged_tags)}")
    
    def _drop_electronic_if_others_present(self, genres: List[str]) -> List[str]:
        """
        Post-processing rule for merged genres (variant A for "Electronic"):
        - If the merged genre list contains "Electronic" AND at least one other genre,
          remove "Electronic" and keep the other genres.
        - If "Electronic" is the only genre, keep it as a fallback.
        
        This rule runs BEFORE GenericSuppressor logic.
        
        Args:
            genres: List of genre strings (may contain duplicates, whitespace, etc.)
            
        Returns:
            Filtered list of genres with "Electronic" removed if other genres exist
        """
        if not genres:
            return genres
        
        # Normalize and filter empty values
        norm = [g.strip() for g in genres if g and g.strip()]
        
        if not norm:
            return genres  # Return original if all were empty
        
        # Check if "Electronic" is present and if there are other genres
        has_electronic = any(g.lower() == "electronic" for g in norm)
        
        if has_electronic and len(norm) > 1:
            # Remove "Electronic" but keep others
            filtered = [g for g in norm if g.lower() != "electronic"]
            utils.log_debug(f"Collector: Dropped 'Electronic' (variant A rule) - had {len(norm)} genres, keeping {len(filtered)}")
            return filtered
        else:
            # Keep "Electronic" if it's the only genre, or if it's not present
            return norm

    def _soft_normalize_comment_tokens(self, tags: List[str]) -> List[str]:
        """
        Soft-normalize tags for deterministic comment output.

        Example:
          input:  ['electronic','psy-chill','psychill','psydub','Downtempo','Ambient']
          output: ['ambient','downtempo','electronic','psy-chill','psychill','psydub']
        """
        if not tags:
            return []

        out = set()
        for t in tags:
            s = str(t).strip().lower()
            if not s:
                continue

            # collapse separators (space|underscore|hyphen) -> hyphen, collapse repeats
            s = re.sub(r"[\s_-]+", "-", s)
            # keep only [a-z0-9-+]
            s = re.sub(r"[^a-z0-9\-+]", "", s)
            # trim leftover separators
            s = s.strip("-")

            if s:
                out.add(s)

        return sorted(out)

    def _get_generic_terms(self) -> Set[str]:
        """
        Build GENERIC_TERMS set (shared by genre and style filtering).

        Definition:
          - normalize to lowercase + trim
          - check membership in GENERIC_TERMS
        """
        raw = _get_config(
            "uma_generic_genres",
            "Electronic\nElectronica\nElectronic Music\nRock\nPop",
        ) or ""
        out: Set[str] = set()
        for line in str(raw).splitlines():
            term = line.strip().lower()
            if term:
                out.add(term)
        return out

    def _is_generic_term(self, term: str, generic_terms: Set[str]) -> bool:
        """
        is_generic(term): Check if term matches any generic pattern.
        
        Matching behavior:
        - Normalize term to lowercase + trim
        - Check exact match OR substring match (if pattern contains wildcard-like indicators)
        - Patterns with '*' are treated as substring patterns (case-insensitive)
        - Patterns without '*' are exact matches (case-insensitive)
        
        Args:
            term: Tag string to check
            generic_terms: Set of normalized generic patterns
            
        Returns:
            True if term matches any generic pattern
        """
        if not term:
            return False
        
        term_norm = str(term).strip().lower()
        if not term_norm:
            return False
        
        # Check exact match first
        if term_norm in generic_terms:
            return True
        
        # Check substring/wildcard patterns
        for pattern in generic_terms:
            # If pattern contains '*', treat as substring pattern
            if '*' in pattern:
                # Remove '*' and check if pattern is substring of term
                pattern_clean = pattern.replace('*', '')
                if pattern_clean and pattern_clean in term_norm:
                    return True
            # If pattern doesn't contain '*', we already checked exact match above
            # But also check if term is substring of pattern (for cases like "Electronic Music")
            elif pattern in term_norm or term_norm in pattern:
                return True
        
        return False

    def _filter_styles_with_generic_terms(self, styles: List[str], generic_terms: Set[str]) -> List[str]:
        """filter_styles(styles): ALWAYS drop generic terms."""
        if not styles:
            return []
        # preserve order, keep existing dedupe behavior (already deduped upstream)
        return [s for s in (x.strip() for x in styles if x and str(x).strip()) if not self._is_generic_term(s, generic_terms)]

    def _select_final_cluster(self, candidate_clusters: Set[str], cluster_map: Dict[str, List[str]]) -> Optional[str]:
        """
        Select final genre cluster using config-driven priority or fallback algorithm.
        
        Algorithm:
        1. Get cluster_priority from MappingEngine (config-driven)
        2. If cluster_priority is non-empty:
           a. Iterate priority list in order, pick first cluster present in candidates
           b. If none from priority list: use Electronica as fallback if available
           c. Else: use deterministic fallback (first in sorted(candidates))
        3. If cluster_priority is empty (backward compatibility):
           a. Use existing hardcoded behavior (generic vs non-generic split)
        
        Args:
            candidate_clusters: Set of cluster names that were mapped from tags
            cluster_map: Dictionary mapping cluster_name -> [source_tags] for logging
            
        Returns:
            Selected cluster name, or None if no valid candidate
        """
        if not candidate_clusters:
            return None
        
        # Get config-driven cluster priority
        cluster_priority = self.mapping_engine.get_cluster_priority()
        
        if cluster_priority:
            # Config-driven selection
            utils.log_debug(f"Collector: Using config-driven cluster priority ({len(cluster_priority)} items)")
            
            # Iterate priority list, pick first match
            for priority_cluster in cluster_priority:
                # Check exact match (case-sensitive for cluster names)
                if priority_cluster in candidate_clusters:
                    source_tags = cluster_map.get(priority_cluster, [])
                    utils.log_debug(
                        f"Collector: Selected '{priority_cluster}' (config priority) from tags: {source_tags[:5]}"
                    )
                    return priority_cluster
            
            # No match from priority list
            # Fallback 1: Electronica if available
            electronica_candidates = [c for c in candidate_clusters if c.strip().lower() == "electronica"]
            if electronica_candidates:
                electronica = electronica_candidates[0]
                source_tags = cluster_map.get(electronica, [])
                utils.log_debug(
                    f"Collector: Selected '{electronica}' (Electronica fallback) from tags: {source_tags[:5]}"
                )
                return electronica
            
            # Fallback 2: Deterministic (first in sorted)
            sorted_candidates = sorted(candidate_clusters)
            if sorted_candidates:
                winner = sorted_candidates[0]
                source_tags = cluster_map.get(winner, [])
                utils.log_debug(
                    f"Collector: Selected '{winner}' (deterministic fallback) from tags: {source_tags[:5]}"
                )
                return winner
            
            return None
        else:
            # Backward compatibility: use existing hardcoded behavior
            utils.log_debug("Collector: No cluster priority in config, using default hardcoded behavior")
            
            # Define hardcoded priority order (most specific to most generic)
            CLUSTER_PRIORITY = [
                "Prog-Psy",
                "Psybient",
                "Psychill",
                "PsyDub",
                "Prog-Trance",
                "Techno",
                "Melodic House & Techno",
                "Chillhouse",
                "Organic",
                "Downtempo",
                "Ambient",
                "Electronica",  # Only as fallback
            ]
            
            # Split into non-generic vs generic candidates
            GENERIC_CLUSTERS = {"Electronica", "electronica"}
            
            non_generic_candidates = set()
            generic_candidates = set()
            
            for cluster in candidate_clusters:
                cluster_norm = cluster.strip()
                if cluster_norm.lower() in GENERIC_CLUSTERS:
                    generic_candidates.add(cluster_norm)
                else:
                    non_generic_candidates.add(cluster_norm)
            
            utils.log_debug(
                f"Collector: Cluster selection (default) - non_generic={sorted(non_generic_candidates)}, "
                f"generic={sorted(generic_candidates)}"
            )
            
            # If we have non-generic candidates, choose highest priority
            if non_generic_candidates:
                for priority_cluster in CLUSTER_PRIORITY:
                    if priority_cluster in non_generic_candidates:
                        source_tags = cluster_map.get(priority_cluster, [])
                        utils.log_debug(
                            f"Collector: Selected '{priority_cluster}' (default priority) from tags: {source_tags[:5]}"
                        )
                        return priority_cluster
            
            # Fallback: use Electronica if available
            if generic_candidates:
                electronica = next((c for c in generic_candidates if c.lower() == "electronica"), None)
                if electronica:
                    source_tags = cluster_map.get(electronica, [])
                    utils.log_debug(
                        f"Collector: Selected '{electronica}' (default fallback) from tags: {source_tags[:5]}"
                    )
                    return electronica
            
            return None

    def _get_priority_list(self, config_key, default):
        try:
            val = _get_config(config_key)
            if val:
                return [s.strip().lower() for s in val.split(',')]
            return default
        except Exception:
            return default

    def _merge_field(self, field_name: str, priority_list: List[str], normalized_sources: Dict) -> List[str]:
        """Generic merge for a list field (styles)."""
        merged = []
        seen = set()
        
        for source_name in priority_list:
            if source_name in normalized_sources:
                block = normalized_sources[source_name]
                values = getattr(block, field_name, [])
                for val in values:
                    norm = utils.normalize_tag(val)
                    if norm and norm not in seen:
                        seen.add(norm)
                        merged.append(val)
        
        return merged

    def _maybe_finalize_album(self, album):
        """
        Check if all sources are resolved, and if so, perform final merge and apply.
        This ensures merge/apply happens exactly once after all sources complete.
        """
        if self.finalized:
            utils.log_debug(f"Collector: Album '{self.album_title}' already finalized, skipping")
            return
        
        if not self._all_sources_resolved():
            utils.log_debug(f"Collector: Waiting for other sources for album '{self.album_title}', pending={self.pending_sources}")
            return
        
        # All sources resolved - perform final merge and apply
        utils.log_debug(f"Collector: All sources resolved for album '{self.album_title}', finalizing")
        self.merge()
        self.apply_to_album(album)
        self.finalized = True
    
    def apply_to_album(self, album):
        """
        Apply merged metadata to Picard album and its tracks.
        Note: This should only be called from _maybe_finalize_album() after all sources are resolved.
        
        This method:
        - Applies metadata to track objects if they exist
        - Also applies to album-level metadata to ensure propagation to tracks created later
        - Handles the case where tracks are not yet populated (files linked later)
        """
        if self.applied:
            utils.log_debug(f"Collector: Album '{self.album_title}' already applied. Skipping.")
            return

        utils.log_info(f"Applying merged metadata to album '{self.album_title}'")
        
        # Check what we have to apply
        has_genres = bool(self.merged_genres)
        has_styles = bool(self.merged_styles)
        has_comment = bool(self.merged_comment)
        
        if not has_genres and not has_styles and not has_comment:
            utils.log_info(f"No metadata changes to apply for album '{self.album_title}'")
            utils.log_info(f"Applied metadata to 0 tracks.")
            self.applied = True
            return
        
        # Get track and file counts for diagnostics
        track_objects = getattr(album, 'tracks', [])
        num_track_objects = len(track_objects) if track_objects else 0
        
        # Get file objects for metadata updates
        file_objects = []
        try:
            if hasattr(album, 'files') and album.files:
                file_objects = list(album.files)
            elif hasattr(album, 'iterfiles'):
                file_objects = list(album.iterfiles())
        except Exception as e:
            utils.log_debug(f"Collector: Could not get files: {e}")
        
        num_attached_files = len(file_objects)
        
        # Build list of metadata targets (tracks first, then files if no tracks)
        metadata_targets = []
        target_descriptions = []
        
        # Prefer track objects if available
        if track_objects:
            for track in track_objects:
                try:
                    if hasattr(track, 'metadata') and track.metadata:
                        metadata_targets.append(track.metadata)
                        track_title = track.metadata.get('title', 'unknown track')
                        target_descriptions.append(f"track '{track_title}'")
                except Exception as e:
                    utils.log_debug(f"Collector: Could not get metadata from track: {e}")
        
        # If no track objects, use file metadata
        if not metadata_targets and file_objects:
            for file_obj in file_objects:
                try:
                    if hasattr(file_obj, 'metadata') and file_obj.metadata:
                        metadata_targets.append(file_obj.metadata)
                        file_name = getattr(file_obj, 'filename', 'unknown file')
                        target_descriptions.append(f"file '{file_name}'")
                except Exception as e:
                    utils.log_debug(f"Collector: Could not get metadata from file: {e}")
        
        num_metadata_targets = len(metadata_targets)
        
        utils.log_info(f"UMA: Apply: album '{self.album_title}' has {num_track_objects} track objects, {num_attached_files} attached files, {num_metadata_targets} metadata targets")
        
        # Compute merge summary before applying
        tracks_with_updates = 0
        example_track_metadata = None
        example_track_title = None
        
        # Apply to metadata targets
        for idx, metadata in enumerate(metadata_targets):
            try:
                target_desc = target_descriptions[idx] if idx < len(target_descriptions) else f"target {idx+1}"
                track_title = metadata.get('title', 'unknown track')
                track_updated = False
                
                # Check what will be applied
                track_metadata_preview = {}
                
                # Apply Genre
                if has_genres:
                    self._apply_field(metadata, "genre", self.merged_genres, "uma_mode_genre")
                    track_metadata_preview['genre'] = self.merged_genres
                    track_updated = True
                
                # Apply Style
                if has_styles:
                    self._apply_field(metadata, "style", self.merged_styles, "uma_mode_style")
                    track_metadata_preview['style'] = self.merged_styles
                    track_updated = True
                
                # Apply Comment
                if has_comment:
                    self._apply_comment(metadata, self.merged_comment, "uma_mode_comment")
                    track_metadata_preview['comment'] = self.merged_comment
                    track_updated = True
                
                if track_updated:
                    tracks_with_updates += 1
                    # Store first updated track as example
                    if example_track_metadata is None:
                        example_track_metadata = track_metadata_preview
                        example_track_title = track_title
            except Exception as e:
                utils.log_warning(f"Collector: Error applying metadata to {target_desc}: {e}")
        
        # Also apply to album-level metadata to ensure propagation to tracks created later
        # This is important when tracks are not yet populated
        album_metadata_updated = False
        try:
            if hasattr(album, 'metadata') and album.metadata:
                album_metadata = album.metadata
                
                # Apply Genre to album metadata
                if has_genres:
                    self._apply_field(album_metadata, "genre", self.merged_genres, "uma_mode_genre")
                    album_metadata_updated = True
                
                # Apply Style to album metadata
                if has_styles:
                    self._apply_field(album_metadata, "style", self.merged_styles, "uma_mode_style")
                    album_metadata_updated = True
                
                # Apply Comment to album metadata
                if has_comment:
                    self._apply_comment(album_metadata, self.merged_comment, "uma_mode_comment")
                    album_metadata_updated = True
                
                if album_metadata_updated:
                    utils.log_debug(f"Collector: Applied metadata to album-level metadata for '{self.album_title}' (will propagate to tracks when created)")
        except Exception as e:
            utils.log_warning(f"Collector: Error applying metadata to album metadata: {e}")
        
        # Log merge summary
        # Use metadata target count if available
        total_targets = num_metadata_targets if num_metadata_targets > 0 else (1 if album_metadata_updated else 0)
        
        if num_metadata_targets > 0:
            utils.log_info(f"Merge summary for album '{self.album_title}': tracks_with_updates={tracks_with_updates} / total_tracks={num_metadata_targets}")
        elif album_metadata_updated:
            utils.log_info(f"Merge summary for album '{self.album_title}': applied to album metadata (tracks will inherit when created)")
            tracks_with_updates = 1  # Indicate that metadata was applied
        else:
            utils.log_info(f"Merge summary for album '{self.album_title}': no metadata targets available")
        
        if example_track_metadata:
            utils.log_info(f"Example merged metadata for track \"{example_track_title}\": {example_track_metadata}")
        elif album_metadata_updated:
            utils.log_info(f"Applied metadata to album-level metadata for '{self.album_title}'")
        else:
            utils.log_info(f"No metadata changes to apply for album '{self.album_title}'")
                
        self.applied = True
        utils.log_info(f"Applied metadata to {tracks_with_updates} tracks.")

    def _apply_field(self, metadata, tag_name, values: List[str], mode_config_key):
        """
        Apply values to a metadata field based on overwrite mode.
        Modes: 'keep', 'overwrite', 'append'
        Default: 'append'
        
        Uses Picard's Metadata API:
        - metadata.get(key, default) returns the value (can be list or single value)
        - metadata[key] = value sets the value
        """
        if not values:
            return

        mode = _get_config(mode_config_key, "append")
        
        # Get current values - normalize to list format
        current_raw = metadata.get(tag_name, [])
        if not isinstance(current_raw, list):
            # If it's a single value, convert to list
            current_values = [current_raw] if current_raw else []
        else:
            current_values = current_raw
        
        if not current_values:
            # If empty, just set it
            metadata[tag_name] = values
        else:
            if mode == "overwrite":
                metadata[tag_name] = values
            elif mode == "append":
                # Append new values if not present
                current_set = set(utils.normalize_tag(v) for v in current_values)
                new_values = current_values[:]  # Copy existing
                for v in values:
                    if utils.normalize_tag(v) not in current_set:
                        new_values.append(v)
                metadata[tag_name] = new_values
            elif mode == "keep":
                pass # Do nothing

    def _apply_comment(self, metadata, comment_text, mode_config_key):
        """
        Apply comment text.
        """
        if not comment_text:
            return
            
        mode = _get_config(mode_config_key, "append")
        tag_name = "comment"
        
        current = metadata.get(tag_name, "")
        
        if not current:
            metadata[tag_name] = comment_text
        else:
            if mode == "overwrite":
                metadata[tag_name] = comment_text
            elif mode == "append":
                if comment_text not in current: # Simple substring check for now
                     metadata[tag_name] = current + " " + comment_text
            elif mode == "keep":
                pass
