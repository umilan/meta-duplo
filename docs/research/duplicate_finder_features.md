# Duplicate finder apps — feature map

As of 2026-10-21. 120 existing features and 54 wished features, bug fixes and proposals, mapped from changelogs, docs, issue trackers, forums and reviews.

## Feature table

| Feature | Description | App name / source |
| --- | --- | --- |
| **EXISTING FEATURES** | | |
| ***Exact-duplicate detection*** | | |
| Size pre-grouping | Group files by byte size first; a file with a unique size cannot have a duplicate and is never read. | fdupes / jdupes, fclones, dupeGuru, Czkawka |
| Partial (prefix) hash | Hash only the first block (~4 KB) to reject most non-matches before reading whole files. | jdupes, fclones, Czkawka |
| Suffix (end-of-file) hash | Also hash a block at the end of the file, catching files that share a header but differ later. | fclones, Czkawka 12, AllDup (compare end block first) |
| Selectable full-content hash | Choose the hash: MD5, SHA-1/256/512, SHA-3, xxHash/XXH3, BLAKE2b/BLAKE3, Metro, CRC32. | fclones, Czkawka, Duplicate Cleaner, rmlint |
| Byte-by-byte verification | Final binary comparison to rule out hash collisions (paranoid mode). | jdupes, rmlint (-p), Duplicate Cleaner, AllDup |
| Partial-range hashing | Hash only a chosen byte range, e.g. skip a constant header that differs by an ID. | rmlint (--clamp-low/--clamp-top) |
| Pre-transform before hashing | Run an external command on a copy of each file (e.g. strip EXIF) before comparing. | fclones (--transform) |
| Hard-link awareness | Treat hard links / same inode as one file, optionally list or count them, or find hard links explicitly. | fclones, Czkawka, dupeGuru, Duplicate Cleaner, AllDup, Tidy Up 5 |
| ***Metadata-insensitive exact matching*** | | |
| Exact image data, ignoring metadata | Same pixels even when EXIF/IPTC differ. | Duplicate Cleaner Pro (Image mode) |
| Exact audio data, ignoring tags | Skip ID3/FLAC metadata blocks so re-tagged tracks still match. | AllDup, Duplicate Cleaner Pro |
| Exact video/audio stream hash | Hash the video and/or audio stream only, ignoring container metadata. | Duplicate Cleaner Pro (Video mode) |
| ***Attribute / name matching*** | | |
| Same file name | Match by name, optionally case-insensitive. | Czkawka, AllDup, Duplicate Cleaner |
| Fuzzy file name | Word-overlap %, per-field comparison, word weighting, similar-word (edit distance) matching. | dupeGuru; AllDup (partial % / leading chars); Duplicate Cleaner (similar name) |
| Name + size combined | Fast mode combining name and byte size. | Czkawka 6, AllDup |
| Basename / extension rules | Only match files with the same basename, same extension, or same name without extension. | rmlint (-b, -e, -i), Duplicate Cleaner |
| Copied-name patterns | Match names like 'Copy of…' or '(1)'. | Duplicate Cleaner |
| Dates with tolerance | Same created/modified date within a tolerance window. | Duplicate Cleaner, AllDup |
| Same folder name / relative path | Match only when parent folder names (to a depth) agree. | Duplicate Cleaner |
| ***Image similarity*** | | |
| Perceptual hashes | aHash, dHash, pHash, Blockhash, Median; configurable hash size and resize filter; similarity slider. | Czkawka / Krokiet |
| Grid colour-block comparison | 15×15 average-colour grid compared tile by tile. | dupeGuru (Picture mode) |
| Bitmap and histogram methods | Choose bitmap, histogram, time interval or combinations; tune RGB sensitivity and bitmap size. | PhotoSweeper |
| Time + visual combined | Require photos to be taken within an interval and look alike. | PhotoSweeper (Time+Bitmap); Duplicate Cleaner (camera time with tolerance) |
| Rotation / flip / mirror invariance | Match rotated, mirrored or flipped copies. | Czkawka 12, Duplicate Cleaner |
| Resolution handling | Match across resolutions, or exclude pairs with identical resolution. | Czkawka, dupeGuru |
| EXIF timestamp match | Same DateTimeOriginal = duplicate (warns: edits keep timestamps). | dupeGuru |
| GPS distance | Match images taken within a geographic distance. | Duplicate Cleaner Pro |
| Image tag match | Match on unique ID, user comment and other tags. | Duplicate Cleaner |
| ML / embedding similarity | Neural embeddings find look-alikes beyond hashes (edits, crops, faces). | Immich (CLIP), Immich-Deduper (ResNet152), ShutterSlim, Gemini 2 'similars' |
| Find similar to one image or a sketch | Pick a reference image (or draw) and find look-alikes. | digiKam |
| Live regroup by similarity level | Change the threshold after scanning and regroup instantly from cache. | PhotoSweeper |
| RAW, HEIC, AVIF, JXL support | Decode modern and camera formats; use embedded JPEG preview for RAW. | Czkawka, Duplicate Cleaner (20+ RAW formats) |
| Auto-apply EXIF orientation | Rotate by EXIF before hashing so rotated copies match. | Czkawka 9 |
| ***Audio similarity*** | | |
| Acoustic fingerprint | Compare by sound: first 15 s, 2 min, or whole track. | Duplicate Cleaner, Czkawka 6 |
| Tag matching (exact or approximate) | Artist, title, album, MusicBrainz IDs, AcoustID, etc. | Duplicate Cleaner, Czkawka |
| Duration tolerance | Match tracks of similar length. | Duplicate Cleaner, AllDup |
| Format/bitrate variants in music library | Same song in different format or bitrate. | Gemini 2 |
| ***Video similarity*** | | |
| Sampled-frame comparison | Hash frames from across the video (e.g. 10 frames at 5–95 %) and compare. | Duplicate Cleaner, Video Duplicate Finder, viddup |
| Thumbnail-only match | Compare one thumbnail near the start (fast, rough). | Duplicate Cleaner |
| Black / solid frame skipping | Skip low-variance frames that cause false positives. | viddup |
| Partial clip detection | Find a short clip inside a longer video, with offset; audio fingerprint plus visual confirmation. | Video Duplicate Finder; Czkawka 12 (subclip) |
| AI matching for transformed videos | DINOv2 embeddings catch cropped, mirrored, zoomed or colour-graded copies; local only. | Video Duplicate Finder |
| Similar videos by audio | Compare soundtracks to group videos. | Czkawka 12, Duplicate Cleaner |
| Duration tolerance / window count / crop detect | Tune sampling windows, length tolerance and letterbox detection. | Czkawka 12 |
| Video property columns | Show codec, bitrate, fps, dimensions, duration in results. | Czkawka 11 |
| ***Scope: folders, sources, comparisons*** | | |
| Duplicate folders | Detect whole duplicated directory trees (e.g. Merkle-tree hashing). | dupeGuru, rmlint (-D), Duplicate Cleaner, Gemini 2, Tidy Up |
| Unique files | List files that have no duplicate anywhere. | fclones (--unique), Duplicate Cleaner, Tidy Up 5 |
| Replica-count thresholds | Find files with more than N or fewer than N copies (backup coverage). | fclones (--rf-over / --rf-under) |
| Reference / master folders | Files in protected folders are never offered for removal. | dupeGuru, Czkawka, Duplicate Cleaner, rmlint (//), digiKam |
| Cross-source only | Only report matches between different roots, not within one. | fclones (--isolate), rmlint (-m), AllDup, Duplicate Cleaner |
| Ignore groups inside one folder | Drop groups whose copies all sit in the same folder. | Duplicate Cleaner |
| Scan inside archives | Look inside zip, rar, 7z, tar, gz… | Duplicate Cleaner Pro, AllDup |
| Virtual (offline) folders | Scan against a saved snapshot of a drive that is not connected. | Duplicate Cleaner Pro |
| Phones and portable devices | Scan MTP devices directly. | Duplicate Cleaner Pro, Cisdem |
| Cloud storage | Scan Google Drive, Dropbox, OneDrive without downloading. | Easy Duplicate Finder, Cisdem |
| Email and contacts | Duplicate messages and contacts in Outlook / Mac Mail. | Easy Duplicate Finder |
| Photo / music / Lightroom libraries | Work inside Photos, Lightroom, iTunes/Music libraries. | Gemini 2, Tidy Up 5 (Lightroom), PowerPhotos, PhotoSweeper, Easy Duplicate Finder |
| Exclude items already in app libraries | Skip files that an app library already manages. | Tidy Up |
| Network drives / NAS | Scan shares and mapped drives. | Duplicate Cleaner, AllDup |
| File list from stdin | Pipe a file list from find or another tool. | fclones (--stdin), rmlint |
| ***Filters and exclusions*** | | |
| Size range | Min/max size filters. | fclones, rmlint, Duplicate Cleaner, AllDup |
| Glob and regex path filters | Include/exclude by globs or regular expressions. | fclones; Czkawka (wildcards) |
| Date range / newer-than | Scan only files newer than a timestamp; incremental via stamp file. | rmlint (-N, -n), Duplicate Cleaner |
| Image/video dimension filter | Limit by pixel dimensions. | Duplicate Cleaner Pro |
| Hidden files, .gitignore | Skip hidden files and ignore-listed paths by default. | fclones, rmlint |
| Excluded-item presets | Built-in presets for system and Trash paths. | Czkawka (DEFAULT, $TRASH); Duplicate Cleaner (system protection) |
| Depth / filesystem boundaries | Limit recursion depth; don't cross mount points. | fclones, rmlint (-d, -x), Czkawka |
| Symlink policies | Follow, ignore, or treat symlinks as hard links. | fclones, rmlint |
| EXIF search criteria | Use EXIF fields as search criteria. | Tidy Up 2+ |
| Saved filter library / predefined searches | Reusable filters; 80+ predefined smart searches. | Duplicate Cleaner, Tidy Up 5 |
| ***Performance and sessions*** | | |
| Persistent hash cache | Store hashes; keyed by inode so moves don't invalidate. | fclones (--cache), Czkawka, Video Duplicate Finder, dupeGuru |
| Hashes in extended attributes | Write checksums to file xattrs for faster reruns. | rmlint (--xattr) |
| Device-aware parallel I/O | Tune threads for SSD vs HDD; order reads by physical placement on HDD. | fclones |
| Page-cache friendly reading | Don't evict the user's cached data (Linux). | fclones |
| Save / load / restore sessions | Resume a previous scan or reopen results later. | Duplicate Cleaner Pro, Gemini 2, AllDup, Czkawka (save results) |
| Replay results with new filters | Re-filter a saved JSON result without touching the disk. | rmlint (--replay) |
| Progress, timings, cancel | Size-based progress, scan time measurement, stop mid-hash. | Czkawka 9–11 |
| Diagnostics panel | Show which formats/codecs and FFmpeg are available. | Krokiet 12.0.1 |
| ***Review and selection*** | | |
| Reference + duplicates grouping | One reference per group; promote any file to reference. | dupeGuru |
| Dupes-only view with delta values | Show differences vs reference (size, date) for powerful sorting. | dupeGuru |
| Post-scan result filter | Filter results by text or regex over full paths. | dupeGuru |
| Selection assistant / custom select | Mark by path, name, size, date, regex; invert within groups. | Duplicate Cleaner, Czkawka / Krokiet |
| Learning smart selection | Remembers which files you delete and applies the rule next time. | Gemini 2 |
| Rule-based keeper choice | Keep largest / highest bitrate / most EXIF / oldest / path-matching / first-named path. | Immich, Immich-Deduper, Video Duplicate Finder (--action), rmlint (-S), fclones (--priority) |
| Keep N copies | Leave a set number of replicas in each group. | fclones (-n) |
| Side-by-side compare | Face-to-face view, swap images, visual-difference tool. | PhotoSweeper, Duplicate Cleaner, Krokiet |
| Thumbnails and media preview | Thumbnail grid, video thumbnails, video preview, audio player. | Duplicate Cleaner, Krokiet, Video Duplicate Finder |
| Difference icons | Icons showing how two files differ (size, date, location). | Gemini 2 |
| Smart Baskets | Custom buckets to sort found items by rules. | Tidy Up |
| Results by file kind | Separate tabs for music, images, PDFs, folders… | Tidy Up 5 |
| Ignore list | Permanently hide a specific match from future scans. | dupeGuru |
| Keyboard-driven review | Space to mark, arrows to move, Enter to open. | Czkawka / Krokiet, dupeGuru |
| ***Actions and safety*** | | |
| Trash by default | Send to Trash/Recycle Bin instead of permanent delete. | Krokiet 11, Gemini 2, dupeGuru |
| Move or copy, preserving paths | Relocate duplicates; optional rename on conflict. | dupeGuru, Krokiet 12, Duplicate Cleaner |
| Replace with links | Hard link, symlink, Windows shortcut or macOS alias. | dupeGuru, Duplicate Cleaner, Tidy Up 5, fclones, Czkawka |
| Reflink / copy-on-write dedupe | Share data blocks while keeping both files (Btrfs, XFS, APFS). | fclones (dedupe), rmlint (clone / reflink) |
| Generated removal script | Write a shell/Python/batch script to review before running. | rmlint (sh, py), Duplicate Cleaner (batch) |
| Dry run | Print what would happen without changing anything. | fclones, Czkawka CLI, Video Duplicate Finder CLI |
| Custom command per file | Run your own command on each selected duplicate. | dupeGuru, rmlint |
| Never delete every copy | Warn or block when all files in a group are marked. | Czkawka, Duplicate Cleaner, dupeGuru |
| Re-check before acting | Abort if the target changed since the scan. | Czkawka 10 |
| Undo / put back | Restore removed files. | Easy Duplicate Finder, Gemini 2, Apple Photos (Recently Deleted) |
| Merge metadata into kept copy | Copy albums, favourites, ratings, descriptions to the keeper. | Immich, Apple Photos (Merge), Immich-Deduper |
| Stack instead of delete | Group versions (RAW+JPEG, edits, Live Photos) under one item. | Immich, PhotoPrism |
| Remove empty folders | Clean up folders left empty after removal. | Duplicate Cleaner, Czkawka |
| Library-safe workflow | Put candidates in an album/keyword so the library app does the deleting. | PowerPhotos, PhotoSweeper, Gemini 2 |
| Parallel actions with progress | Multithreaded delete/move/link with stop button. | Krokiet 10 |
| ***Output, automation and platform*** | | |
| Export results | CSV, JSON, HTML/XHTML, TXT. | fclones, rmlint, dupeGuru, Czkawka, AllDup, Tidy Up, Duplicate Cleaner |
| fdupes-compatible output | Output format other scripts already understand. | fclones, rmlint |
| Command-line launch | Start scans from CLI or scripts. | AllDup, Duplicate Cleaner, Czkawka CLI, Video Duplicate Finder CLI, Krokiet 12 |
| Web UI and Docker | Run headless on a NAS, use from a browser. | Video Duplicate Finder |
| Shell integration | Explorer/Finder context menu, menu bar. | AllDup, Gemini 2 |
| Duplicate monitor | Watch for new duplicates as files arrive. | Gemini 2 |
| Import-time blocking | Skip byte-identical files when importing. | Apple Photos, PhotoPrism, Immich |
| Themes and translations | Dark/light themes, many UI languages. | Krokiet, Duplicate Cleaner Pro |
| Completion notification | System notification or sound when a long scan finishes. | Krokiet 11–12 |
| Mobile app | Duplicate cleaning on Android / iOS. | Cedinia (Czkawka), Gemini Photos |
| Action log | Detailed log of everything done. | AllDup |
| ***Adjacent cleanup tools (often bundled)*** | | |
| Broken / corrupted files | Detect damaged images, audio, video, PDFs, archives, fonts. | Czkawka |
| Other lint | Empty files/folders, big files, temp files, bad extensions, bad names, broken symlinks, bad user IDs. | Czkawka, rmlint |
| EXIF remover and video optimizer | Strip tags; re-encode videos and crop black bars. | Czkawka 11 |
| **WISHED FEATURES, BUG FIXES AND PROPOSALS** | | |
| ***Feature requests from users*** | | |
| Read files in physical disk order | On HDDs with many small files, read in on-disk order to cut seek time. | Czkawka issue #1176 (fclones already does this on Linux) |
| Import a saved results list | Load an exported duplicate list back into the app to continue. | Czkawka issue #1295 |
| Use FLAC's built-in audio MD5 | Use the stream checksum stored in FLAC headers to match music instantly. | Czkawka issue list |
| Start offset for similar videos | Skip a fixed intro so episodes with identical openings are not matched. | Czkawka feature request; vid_dup_finder_lib notes false positives on shared intros |
| Preserve file attributes on move/copy | Keep dates and attributes when relocating duplicates. | Czkawka issue list |
| Show unique (non-duplicate) files | Invert results: list files with no copy — a recurring request. | dupeGuru issue #218 |
| Scan one album / smart album | Limit a scan to a chosen album inside a photo library. | dupeGuru issue #215 |
| Keep the oldest date when merging | On dedupe, keep the earliest capture date across the group. | Immich discussion #27969 |
| Explain metadata sync before it happens | Clear prompt that albums, descriptions and ratings will be copied to kept assets. | Immich discussion #29160 |
| One-click 'Ignore' / 'Not a duplicate' | Dismiss a group without unticking every file; mark as non-duplicate. | Immich discussion #10174; Apple Photos users |
| Adjustable threshold in built-in finders | Let users tune what counts as a duplicate. | Apple Support Community (Photos) |
| Check what's already uploaded | Dry check against the server without uploading anything. | Immich discussion #31624 |
| Transparent metadata conflict resolution | Show which date, location, keywords win when merging. | Apple Support Community (Photos Merge) |
| Burst-aware grouping | Don't group distinct burst frames as duplicates; match bursts across folders correctly. | Duplicate Cleaner forum (Google Takeout bursts); Macworld on Photos flagging bursts |
| Cross-format matching (HEIC ↔ JPG) | Same shot saved as HEIC and JPG should be found. | Mac Power Users forum (Gemini 2 thread) |
| Easy RAW+JPEG pair handling | Find pairs and drop one half, or keep both on purpose. | The Robservatory / Kirkville; Immich-Deduper option |
| Edit smart-selection rules directly | Toggle or create rules (e.g. 'smaller dimensions') instead of waiting for learning. | Gemini 2 App Store review |
| Grey out / disregard kept similars | Mark items as intentionally kept so they stop reappearing. | Gemini 2 user review |
| Scheduled scans | Run scans of folders/volumes on a schedule. | Macworld review of Gemini 2 |
| Compare two drives, cross-drive only | List copies existing on both drives, ignoring copies within one. | voidtools (Everything) forum; feature in development |
| UI to browse and delete duplicates | Review duplicates in the web UI; surface similar but un-stacked images. | PhotoPrism docs (planned); PhotoPrism PR #1457 |
| Stack visual variants in cloud photo apps | Collapse low-res/high-res/edited copies into one stack. | photo_stack_finder (Google Photos request) |
| Regex include/exclude in core tool | Built-in regex filters (declined in favour of piping from find). | rmlint docs (frequent request) |
| Fewer partial-clip false positives | Better defaults and guidance for matching short clips to sources. | Video Duplicate Finder discussion #737 |
| Path selection that understands drive letters | Selecting by path on Windows drive letters / mounted remotes. | rclone forum (Czkawka user) |
| Forced review before 'delete all' | Make users look at groups before mass deletion. | Habr article (lost photos after 'Delete all') |
| Organiser + duplicates in one | Sort by date, place, faces, and gather duplicates in one album. | Habr article ('ideal duplicate killer') |
| Migration completeness check | Prove every file from the source library is present after import. | PhotoPrism discussion #3126 |
| ***Unresolved bugs and pain points*** | | |
| Fix: app fails on newer Python | dupeGuru packages crash on start with newer system Python; open about four years. | dupeGuru issue #1066 |
| Fix: obvious look-alikes not matched | Picture mode misses clearly similar images while matching wrong ones. | dupeGuru issue #1229 |
| Fix: memory blow-up on huge scans | 31 GB RAM on a 400k-file network mount before any result appeared. | rclone forum (dupeGuru) |
| Fix: inconsistent results between runs | Similars shown once, then 'nothing found' on the next run. | Gemini 2 user reviews |
| Fix: leftover links double-count space | Hard links left after trashing make disk tools count files twice. | Gemini 2 user review |
| Fix: cloud dedupe not working as advertised | Cloud-file duplicates not handled. | Gemini 2 user review |
| Fix: unwanted background deletions (reported) | Users report photos moved to trash without consent. | Gemini 2 user review |
| Fix: false duplicates of intentional edits | Built-in finder flags deliberate crops / B&W versions. | Apple Support Community (Photos) |
| Fix: can't remove a group without merging | No way to dismiss a false group from the Duplicates album. | Apple Support Community thread 254644379 |
| Fix: slow, invisible detection | Duplicates album appears only after hours/days of indexing. | Apple Support Community; Mac forums |
| Fix: library damage by generic finders | Generic file dedupers can corrupt photo libraries; tools must be library-aware. | Apple Support Community (citing Apple support note) |
| Fix: thumbnails show original, not edit | Hard to know if you delete the edited version. | Apple Support Community thread 252379013 |
| Fix: unrelated photos stacked together | Same-name stacking cannot be disabled; unrelated images merged in one stack. | PhotoPrism discussion #5136 |
| Fix: settings lost on Cmd+Q (macOS) | Preferences not persisted when quitting via keyboard. | Czkawka / Krokiet issue list |
| Fix: UI rendering glitches | Intermittent black screen; text disappears with some system languages. | Czkawka / Krokiet issue list |
| ***Potentially useful (not found in surveyed apps)*** | | |
| Explained, calibrated confidence | Show why files matched (evidence per signal) and a confidence learned from user labels. | Proposal; reviewers ask tools to explain why files matched (Softorino) |
| Per-method cost estimate | Benchmark disk and decode speed, then show time and marginal cost per method before scanning. | Proposal |
| Folder overlap view | Classify folder pairs (A ≡ B, A ⊂ B, A ∩ B %) with a heatmap of shared size. | Proposal (extends duplicate-folder modes) |
| Same pixels, different metadata — with diff | Group metadata-only variants and let the user pick the best metadata. | Proposal (extends Duplicate Cleaner's ignore-metadata modes) |
| Ignore keyed by content hash | Ignored pairs survive renames, moves and re-scans. | Proposal |
| 'Not a duplicate' labels improve scoring | Use user verdicts to recalibrate thresholds. | Proposal |
| Action basket with summary and undo log | Collect actions, show bytes freed and per-verdict counts, apply, undo. | Proposal |
| Treat Live Photos, RAW+JPEG and sidecars as units | Never flag the partner file of a pair as a duplicate. | Proposal (cf. PhotoPrism stacks) |
| Volume-aware cache for external disks | Key cache by disk UUID so a re-mounted disk hits the cache. | Proposal |
| Regex rules on metadata fields | Exclude/include by regex on EXIF/QuickTime tags (Software, Model…). | Proposal (extends Tidy Up EXIF criteria) |
| Resolve true capture date before matching | Use filename dates and sidecar JSON (e.g. Google Takeout) when EXIF is missing. | Proposal (cf. Duplicate Cleaner forum Takeout case) |

## Duplicate finder apps surveyed

| App | Platform | Type | Notes |
| --- | --- | --- | --- |
| fdupes | Linux, macOS, Windows | CLI, open source | Classic exact-duplicate finder |
| jdupes | Linux, macOS, Windows | CLI, open source | fdupes fork; size → partial → full hash → byte compare |
| rmlint | Linux, macOS | CLI + GUI, open source | Duplicate dirs, script output, replay, reflink |
| fclones / fclones-gui | Linux (best), macOS, Windows | CLI + GUI, open source | Very fast; reflink dedupe; JSON/CSV |
| rdfind | Linux, macOS | CLI, open source | Exact duplicates, link replacement |
| yadf | Cross-platform | CLI, open source | Rust exact finder |
| dupe-krill | Cross-platform | CLI, open source | Rust, hard-link dedupe |
| FSlint | Linux | GUI, open source (dated) | Predecessor of Czkawka |
| Czkawka / Krokiet | Linux, macOS, Windows | GUI + CLI, open source | Images, video, music, broken files |
| Cedinia | Android | App, open source | Experimental Czkawka mobile app |
| Czkawka Tauri | Cross-platform | GUI, open source (unofficial) | Alternative frontend |
| Detwinner | Linux | GUI, open source | Exact duplicates |
| dupeGuru | Linux, macOS, Windows | GUI, open source | Fuzzy names, picture & music modes |
| AntiDupl.NET | Windows | GUI, open source | Similar images, rich metadata view |
| Video Duplicate Finder | Linux, macOS, Windows, Docker | GUI + CLI + Web, open source | Video/image similarity, partial clips, AI |
| viddup | Cross-platform | CLI, open source | Video pHash, HTML report |
| findimagedupes / immich-duplicates | Linux / Docker | CLI, open source | Image dupes for Immich |
| Immich-Deduper | Docker | Web, open source | ML dedupe for Immich, metadata merge |
| AllDup | Windows | GUI, freeware | Many criteria, similar pictures/music |
| Duplicate Cleaner (Free / Pro) | Windows | GUI, freemium | Image/audio/video modes, archives, virtual folders |
| Fast Duplicate File Finder | Windows | GUI, freemium | MindGems |
| Visual Similarity Duplicate Image Finder | Windows | GUI, paid | MindGems, similar images |
| Auslogics Duplicate File Finder | Windows | GUI, freemium |  |
| Wise Duplicate Finder | Windows | GUI, freemium |  |
| Ashisoft Duplicate File Finder | Windows | GUI, freemium |  |
| Duplicate File Detective | Windows | GUI, paid | Enterprise oriented |
| CloneSpy | Windows | GUI, freeware | Thorough content compare |
| SearchMyFiles (NirSoft) | Windows | GUI, freeware | Duplicates search mode |
| Glary Duplicate Cleaner | Windows | GUI, freeware |  |
| VisiPics | Windows | GUI, freeware | Similar images |
| Awesome Duplicate Photo Finder | Windows | GUI, freeware | Similar images |
| Duplicate Photo Cleaner | Windows, macOS | GUI, paid |  |
| Vovsoft Duplicate File Finder | Windows | GUI, paid |  |
| Duplicate File Finder Plus | Windows | GUI, subscription |  |
| Duplicate Files Fixer / Duplicate Photos Fixer Pro | Windows, macOS, Android | GUI, paid | Systweak |
| BreviSoft Duplicate File Finder | Windows, macOS | GUI, paid |  |
| Aryson Duplicate File Finder | Windows | GUI, paid |  |
| Advanced SystemCare (built-in finder) | Windows | Suite | IObit |
| CCleaner (Duplicate Finder) | Windows, macOS | Suite |  |
| Duplicate Sweeper | Windows, macOS | GUI, paid |  |
| Easy Duplicate Finder | Windows, macOS | GUI, paid | Cloud, email, contacts, folder compare |
| PictureEcho | Windows | GUI, subscription | Similar images |
| SimilarImages | Windows | GUI, discontinued | Quadrant RGB compare |
| Video Simili Duplicate Cleaner | Windows | GUI, paid | Similar videos |
| Dup Detector / Duplicate Image Detector | Windows | GUI | Pixel-level image compare |
| Directory Report | Windows | GUI, paid | File manager with duplicate finder |
| FolderMatch | Windows | GUI, paid | Folder compare + duplicates |
| XnView | Windows, macOS, Linux | Image viewer | Built-in duplicate finder |
| Gemini 2 | macOS | GUI, subscription | Similars, smart selection, Photos/Music |
| Gemini Photos | iOS | App, subscription | Similar, screenshots, blurred |
| PhotoSweeper | macOS | GUI, paid | Six comparison methods, face-to-face |
| Tidy Up | macOS | GUI, paid | Smart Baskets, Lightroom, 80+ presets |
| Duplicate File Finder (Nektony) | macOS | GUI, freemium | Similar photos, folders |
| Cisdem Duplicate Finder | macOS, Windows | GUI, paid | Devices, OneDrive, Dropbox |
| Duplicate Detective | macOS | GUI, paid |  |
| CleverFiles Duplicates Finder / Disk Drill | macOS | GUI, paid |  |
| CleanMyMac | macOS | Suite | MacPaw, duplicate module |
| PowerPhotos | macOS | GUI, paid | Photos library-safe duplicates |
| ShutterSlim | macOS | GUI, paid | On-device ML, faces |
| ZeroDuplicates | macOS | GUI |  |
| Fireebok Duplicates Finder | macOS | GUI, paid |  |
| Tenorshare Cleamio | macOS | Suite |  |
| Cleaner One Pro (Trend Micro) | macOS, Windows | Suite | Similar photos and videos |
| Apple Photos (Duplicates album) | macOS 13+, iOS 16+ | Built-in | Merge, Recently Deleted |
| Immich | Self-hosted | Photo server | ML duplicate review, stacks |
| PhotoPrism | Self-hosted | Photo server | SHA-1 dedupe, stacks |
| digiKam | Linux, macOS, Windows | Photo manager, open source | Find duplicates, similar, by sketch |
| FileLocator File Finder | Browser | Web tool | Single-folder check in browser |
| Python imagehash / Rust img_hash / vid_dup_finder_lib | Libraries | Open source | Building blocks |

## Sources

- [Czkawka changelog](https://fossies.org/linux/czkawka/Changelog.md)
- [Czkawka repo / releases](https://github.com/qarmin/czkawka/releases)
- [Czkawka issues](https://github.com/qarmin/czkawka/issues)
- [Czkawka issue #1176](https://github.com/qarmin/czkawka/issues/1176)
- [fclones README](https://github.com/pkolaczk/fclones)
- [fclones-gui](https://github.com/pkolaczk/fclones-gui)
- [rmlint docs](https://rmlint.readthedocs.io/en/latest/)
- [rmlint tutorial](https://rmlint.readthedocs.io/en/latest/tutorial.html)
- [jdupes man page](https://man.archlinux.org/man/jdupes.1.en)
- [dupeGuru scan docs](https://dupeguru.voltaicideas.net/help/en/scan.html)
- [dupeGuru results docs](https://dupeguru.voltaicideas.net/help/en/results.html)
- [dupeGuru issue #218](https://github.com/arsenetar/dupeguru/issues/218)
- [dupeGuru issue #215](https://github.com/arsenetar/dupeguru/issues/215)
- [dupeGuru issue #1066](https://github.com/arsenetar/dupeguru/issues/1066)
- [dupeGuru issue #1229](https://github.com/arsenetar/dupeguru/issues/1229)
- [Duplicate Cleaner feature list](https://www.digitalvolcano.co.uk/dcfeatures.html)
- [Duplicate Cleaner forum (bursts)](https://forum.digitalvolcano.co.uk/viewtopic.php?p=5835)
- [AllDup manual](https://alldup.de/en/alldup/documentation/search-methods/file-content.php)
- [Video Duplicate Finder README](https://github.com/0x90d/videoduplicatefinder)
- [VDF discussion #737](https://github.com/0x90d/videoduplicatefinder/discussions/737)
- [viddup](https://pypi.org/project/viddup/)
- [vid_dup_finder_lib](https://awesome.ecosyste.ms/projects/1062345)
- [PhotoSweeper (Macworld)](https://www.macworld.com/article/217457/photosweeper_is_a_simple_and_efficient_utility_for_organizing_photos.html)
- [Gemini 2 App Store](https://apps.apple.com/app/id1090488118)
- [Gemini 2 launch (MacPaw)](https://macpaw.com/news/gemini-2-like-gemini-only-smarter)
- [Gemini 2 reviews (SplitMetrics)](https://splitmetrics.com/apps/gemini-2-the-duplicate-finder/id1090488118)
- [Gemini 2 review (Macworld)](https://www.macworld.com/article/3074939/software-utilities/gemini-2-review-first-line-of-defense-against-duplicate-files-on-your-mac.html)
- [Tidy Up 5 (App Store)](https://apps.apple.com/nl/app/tidy-up/id1395962105?mt=12)
- [Easy Duplicate Finder](https://engelmann.com/en/products/easy-duplicate-finder)
- [Immich duplicates utility](https://docs.immich.app/features/duplicates-utility)
- [Immich discussion #27969](https://github.com/immich-app/immich/discussions/27969)
- [Immich discussion #29160](https://github.com/immich-app/immich/discussions/29160)
- [Immich discussion #10174](https://github.com/immich-app/immich/discussions/10174)
- [Immich discussion #31624](https://github.com/immich-app/immich/discussions/31624)
- [Immich-Deduper](https://github.com/RazgrizHsu/immich-deduper)
- [PhotoPrism duplicates docs](https://docs.photoprism.app/user-guide/library/duplicates)
- [PhotoPrism PR #1457](https://github.com/photoprism/photoprism/pull/1457)
- [PhotoPrism discussion #5136](https://github.com/photoprism/photoprism/discussions/5136)
- [PhotoPrism discussion #3126](https://github.com/photoprism/photoprism/discussions/3126)
- [digiKam similarity view](https://docs.digikam.org/en/left_sidebar/similarity_view.html)
- [Apple Photos merge thread](https://discussions.apple.com/thread/254784519)
- [Apple Photos non-duplicates thread](https://discussions.apple.com/thread/255290955)
- [Apple Photos remove-from-album thread](https://discussions.apple.com/thread/254644379)
- [Apple Photos third-party finders thread](https://discussions.apple.com/thread/8357683?page=1)
- [Apple Photos edited-thumbnail thread](https://origin-discussions2-us-dr-prz.apple.com/thread/252379013)
- [Macworld on Photos duplicate detection](https://www.macworld.com/article/1366211/how-to-use-duplicate-detection-apple-photos.html)
- [RAW+JPEG in Photos (Kirkville)](https://kirkville.com/photos-makes-it-very-difficult-to-find-rawjpeg-photos-the-robservatory/)
- [Mac Power Users forum](https://talk.macpowerusers.com/t/better-alternatives-to-gemini-ii-for-duplicate-finding/14174)
- [voidtools forum (drive compare)](https://voidtools.com/forum/viewtopic.php?p=54332)
- [rclone forum (dupeGuru vs Czkawka)](https://forum.rclone.org/t/does-anyone-know-a-good-local-dedupe-tool-that-can-take-400-000-files-mounted-with-rclone-mount-and-dedupe-them-purely-by-size-and-name/53332)
- [Habr article](https://habr.com/en/articles/990286)
- [photo_stack_finder](https://pypi.org/project/photo-stack-finder/)
- [Softorino roundup](https://softorino.com/blog/top-7-duplicate-file-finders-for-mac-windows)
- [IObit roundup (app list)](https://www.iobit.com/en-blog/duplicate-file-finder.php)
- [SoftwareHow roundup (app list)](https://www.softwarehow.com/best-duplicate-file-finder/)
- [MindGems photo finders list](https://www.mindgems.com/article/wp-json/wp/v2/posts/1460)
- [AlternativeTo dupeGuru alternatives](https://alternativeto.net/software/dupeguru)
- [AlternativeTo PhotoSweeper alternatives](https://alternativeto.net/software/photosweeper)
