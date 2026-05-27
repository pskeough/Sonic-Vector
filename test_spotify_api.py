"""Spotify API Diagnostic Test Runner.

Responsibility: Performs real-world queries against Spotify's Web API,
verifies endpoint status, stores the findings, and triggers report generation.
"""

import logging
import time
from spotify_client import SpotifyAPIClient
from db_reporter import DataReporter

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def run_diagnostics():
    """Run diagnostics on a selection of diverse songs to harvest API data."""
    logger.info("=" * 70)
    logger.info("STARTING SPOTIFY API DIAGNOSTIC HARVESTER")
    logger.info("=" * 70)

    try:
        # 1. Initialize Spotify Client and Database Reporter
        # We specify the config.yaml located in the current isolated folder
        client = SpotifyAPIClient(config_path="config.yaml")
        reporter = DataReporter(db_path="data/test_library.db")

        # 2. Define diverse list of test songs representing various genres and eras
        test_queries = [
            "track:Creep artist:Radiohead",             # Alt/Art Rock (Classic)
            "track:Get Lucky artist:Daft Punk",        # EDM/Electronic/Funk (Modern)
            "track:Lose Yourself artist:Eminem",        # Hip-Hop/Rap (Energetic, Vocal)
            "track:Stairway to Heaven artist:Led Zeppelin" # Classic Progressive Rock (Dynamic)
        ]

        logger.info(f"Loaded {len(test_queries)} test songs for diagnostic harvesting...")

        # 3. Harvest details for each song
        for query in test_queries:
            logger.info("-" * 60)
            logger.info(f"Processing query: '{query}'")
            
            # Step 1: Search track to get track ID
            search_result = client.search_track(query)
            if not search_result or not search_result.get('tracks', {}).get('items'):
                logger.warning(f"  ✗ Could not find track on Spotify for query: {query}")
                continue
                
            track_brief = search_result['tracks']['items'][0]
            track_id = track_brief['id']
            time.sleep(0.2)
            
            # Step 2: Fetch detailed track metadata
            track_data = client.get_track(track_id)
            time.sleep(0.2)
            
            # Step 3: Fetch detailed artist metadata (for genres)
            artist_id = track_data['artists'][0]['id']
            artist_data = client.get_artist(artist_id)
            time.sleep(0.2)
            
            # Step 4: Fetch audio features (may fail gracefully if deprecated)
            features_data = client.get_audio_features(track_id)
            
            # Step 5: Save diagnostics to database
            reporter.save_diagnostics(
                track_data=track_data,
                artist_data=artist_data,
                features_data=features_data
            )
            logger.info(f"  ✓ Successfully harvested and stored: '{track_data['name']}' by {track_data['artists'][0]['name']}")
            time.sleep(0.5)

        logger.info("-" * 60)
        logger.info("All diagnostic queries completed!")

        # 4. Compile the final Markdown report
        logger.info("Compiling data source report...")
        reporter.generate_markdown_report(output_path="docs/spotify_api_report.md")

        logger.info("=" * 70)
        logger.info("SPOTIFY DIAGNOSTIC HARVEST COMPLETED SUCCESSFULLY!")
        logger.info("Database: EmbeddingTestIsolated/data/test_library.db")
        logger.info("Markdown Report: EmbeddingTestIsolated/docs/spotify_api_report.md")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"✗ Diagnostics failed: {e}", exc_info=True)


if __name__ == '__main__':
    run_diagnostics()
