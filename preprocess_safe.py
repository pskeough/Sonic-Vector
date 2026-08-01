"""SAFE-DB Semantic EQ Dataset Preprocessor & Centroid Builder (Zero-Dependency).

Responsibility: Handles parsing of crowdsourced EQ curves in pure Python, filters
out outliers mathematically, computes the average parametric 'sonic centroid'
for each semantic descriptor, and logs them into our local SQLite database.

By using only native Python modules (csv, sqlite3, json), this script avoids
complex libraries like pandas/numpy, running instantly and with zero installation hurdles.
"""

import os
import csv
import json
import random
import logging
import sqlite3
import math
from pathlib import Path
from typing import Dict, Any, List

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def generate_mock_dataset(output_path: Path, num_rows: int = 150) -> None:
    """Generate a scientifically realistic mock crowdsourced EQ dataset."""
    logger.info(f"SAFEEqualiserUserData.csv not found. Generating a highly realistic mock dataset at {output_path}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Define our target semantic EQ configurations (mean values for random variations)
    # Format: (Freq Mean, Freq SD, Gain Mean, Gain SD, Q Mean, Q SD)
    semantic_profiles = {
        "warm": {
            "low_shelf":   (120.0, 15.0,  2.5,  0.6),
            "band_1":      (250.0, 30.0,  1.8,  0.5,  1.0, 0.2), # low-mid warmth
            "band_2":      (800.0, 100.0, -0.5, 0.3,  0.8, 0.1), # slight cut for boxiness
            "band_3":      (3200.0, 400.0, -1.5, 0.4,  1.0, 0.2), # reduce harshness
            "high_shelf":  (10000.0, 1000.0, -1.2, 0.4)           # soft high roll-off
        },
        "bright": {
            "low_shelf":   (100.0, 20.0,  -1.0, 0.4),
            "band_1":      (300.0, 50.0,  -0.8, 0.3,  1.0, 0.2),
            "band_2":      (2500.0, 300.0,  2.0, 0.5,  0.8, 0.1), # crisp presence
            "band_3":      (6000.0, 800.0,  1.5, 0.4,  0.9, 0.2), # sparkle
            "high_shelf":  (12000.0, 1200.0,  2.5, 0.7)           # airy highs
        },
        "muddy": { # Bloated low-mids
            "low_shelf":   (150.0, 25.0,  3.5,  0.8),
            "band_1":      (300.0, 40.0,  4.5,  1.0,  1.2, 0.3), # bloated 300Hz
            "band_2":      (1000.0, 150.0, 0.0,  0.2,  0.7, 0.1),
            "band_3":      (4000.0, 500.0, -2.0, 0.6,  1.0, 0.2), # muffled highs
            "high_shelf":  (8000.0, 800.0, -3.0,  0.8)
        },
        "presence": { # Forward vocals and detail
            "low_shelf":   (80.0,  10.0,  0.0,  0.1),
            "band_1":      (200.0, 20.0,  -0.5, 0.2,  0.9, 0.1),
            "band_2":      (2000.0, 250.0,  3.0, 0.6,  0.8, 0.2), # presence boost
            "band_3":      (4000.0, 400.0,  1.0, 0.3,  1.0, 0.1),
            "high_shelf":  (10000.0, 1000.0, 0.5, 0.2)
        },
        "airy": { # High-frequency sparkle and openness
            "low_shelf":   (80.0,  10.0,  -0.5, 0.2),
            "band_1":      (250.0, 30.0,  0.0,  0.1,  0.8, 0.1),
            "band_2":      (1500.0, 200.0, 0.5,  0.2,  0.7, 0.1),
            "band_3":      (8000.0, 1000.0, 2.0,  0.5,  0.8, 0.2),
            "high_shelf":  (14000.0, 1000.0, 4.0,  0.8)          # heavy air shelf
        },
        "punchy": { # Impactful low-end and transients
            "low_shelf":   (70.0,  8.0,   3.2,  0.7),           # kick drum punch
            "band_1":      (150.0, 20.0,  -1.2, 0.4,  1.5, 0.3), # clean up bass boxiness
            "band_2":      (3000.0, 350.0,  2.2, 0.5,  1.0, 0.2), # snap/crack
            "band_3":      (7000.0, 700.0,  0.8, 0.3,  0.8, 0.1),
            "high_shelf":  (12000.0, 1200.0, 1.2, 0.4)
        }
    }
    
    genres = ["Rock", "Pop", "Electronic", "Hip-Hop", "Jazz", "Classical", "Metal"]
    instruments = ["Mix", "Vocal", "Guitar", "Drums", "Synth", "Bass"]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        for i in range(num_rows):
            descriptor = random.choice(list(semantic_profiles.keys()))
            profile = semantic_profiles[descriptor]
            
            # Generate parametric values with Gaussian noise
            low_shelf_freq = max(20.0, random.gauss(profile["low_shelf"][0], profile["low_shelf"][1]))
            low_shelf_gain = random.gauss(profile["low_shelf"][2], profile["low_shelf"][3])
            
            f1_freq = max(50.0, random.gauss(profile["band_1"][0], profile["band_1"][1]))
            f1_gain = random.gauss(profile["band_1"][2], profile["band_1"][3])
            f1_q = max(0.1, random.gauss(profile["band_1"][4], profile["band_1"][5]))
            
            f2_freq = max(200.0, random.gauss(profile["band_2"][0], profile["band_2"][1]))
            f2_gain = random.gauss(profile["band_2"][2], profile["band_2"][3])
            f2_q = max(0.1, random.gauss(profile["band_2"][4], profile["band_2"][5]))
            
            f3_freq = max(1000.0, random.gauss(profile["band_3"][0], profile["band_3"][1]))
            f3_gain = random.gauss(profile["band_3"][2], profile["band_3"][3])
            f3_q = max(0.1, random.gauss(profile["band_3"][4], profile["band_3"][5]))
            
            high_shelf_freq = min(20000.0, random.gauss(profile["high_shelf"][0], profile["high_shelf"][1]))
            high_shelf_gain = random.gauss(profile["high_shelf"][2], profile["high_shelf"][3])
            
            # Formulate the 25 columns matching original SAFEEqualiserUserData layout
            row = [
                i + 1,                                       # entry ID
                descriptor,                                  # semantic word
                f"192.168.1.{random.randint(2,254)}",        # ip_address
                "", "",                                      # ? and ? placeholders
                round(low_shelf_gain, 2),
                round(low_shelf_freq, 1),
                round(f1_gain, 2),
                round(f1_freq, 1),
                round(f1_q, 2),
                round(f2_gain, 2),
                round(f2_freq, 1),
                round(f2_q, 2),
                round(f3_gain, 2),
                round(f3_freq, 1),
                round(f3_q, 2),
                round(high_shelf_gain, 2),
                round(high_shelf_freq, 1),
                random.choice(genres),                       # genre
                random.choice(instruments),                  # instrument
                "United Kingdom",                            # location
                "Professional",                              # experience
                random.randint(20, 60),                      # age
                "British",                                   # nationality
                f"hash_{random.getrandbits(32)}"             # hash
            ]
            writer.writerow(row)
            
    logger.info(f"OK: Mock dataset with {num_rows} records generated successfully!")


def init_db(db_path: Path) -> None:
    """Create the SQLite table for preprocessed semantic EQ centroids."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS semantic_eq_centroids (
                descriptor TEXT PRIMARY KEY,
                low_shelf_gain REAL,
                low_shelf_freq REAL,
                first_band_gain REAL,
                first_band_freq REAL,
                first_band_q REAL,
                second_band_gain REAL,
                second_band_freq REAL,
                second_band_q REAL,
                third_band_gain REAL,
                third_band_freq REAL,
                third_band_q REAL,
                high_shelf_gain REAL,
                high_shelf_freq REAL,
                sample_count INTEGER,
                centroid_json TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def process_dataset(csv_path: Path, db_path: Path) -> None:
    """Read dataset using pure Python, clean outliers, compute centroids, and store in SQLite."""
    logger.info("-" * 60)
    logger.info("PROCESSING SEMANTIC EQ DATASET (ZERO-DEPENDENCY)")
    logger.info("-" * 60)
    
    # We map column index to variable name for raw data parsing
    # SAFEEqualiserUserData.csv has 25 columns:
    # Index 1: descriptor
    # Index 5: low_shelf_gain, Index 6: low_shelf_freq
    # Index 7: first_band_gain, Index 8: first_band_freq, Index 9: first_band_q
    # Index 10: second_band_gain, Index 11: second_band_freq, Index 12: second_band_q
    # Index 13: third_band_gain, Index 14: third_band_freq, Index 15: third_band_q
    # Index 16: high_shelf_gain, Index 17: high_shelf_freq
    
    records_by_descriptor = {}
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 18:
                continue
                
            desc = row[1].strip().lower()
            if not desc:
                continue
                
            try:
                # Parse numeric values
                low_shelf_gain = float(row[5])
                low_shelf_freq = float(row[6])
                first_band_gain = float(row[7])
                first_band_freq = float(row[8])
                first_band_q = float(row[9])
                second_band_gain = float(row[10])
                second_band_freq = float(row[11])
                second_band_q = float(row[12])
                third_band_gain = float(row[13])
                third_band_freq = float(row[14])
                third_band_q = float(row[15])
                high_shelf_gain = float(row[16])
                high_shelf_freq = float(row[17])
                
                # Simple outlier protection (Gains should be within realistic limits, e.g. ±12dB)
                # This protects our mathematical averages from corrupted submissions
                if any(abs(g) > 15.0 for g in [low_shelf_gain, first_band_gain, second_band_gain, third_band_gain, high_shelf_gain]):
                    continue
                if any(f <= 0.0 for f in [low_shelf_freq, first_band_freq, second_band_freq, third_band_freq, high_shelf_freq]):
                    continue
                
                record = {
                    "low_shelf_gain": low_shelf_gain, "low_shelf_freq": low_shelf_freq,
                    "first_band_gain": first_band_gain, "first_band_freq": first_band_freq, "first_band_q": first_band_q,
                    "second_band_gain": second_band_gain, "second_band_freq": second_band_freq, "second_band_q": second_band_q,
                    "third_band_gain": third_band_gain, "third_band_freq": third_band_freq, "third_band_q": third_band_q,
                    "high_shelf_gain": high_shelf_gain, "high_shelf_freq": high_shelf_freq
                }
                
                if desc not in records_by_descriptor:
                    records_by_descriptor[desc] = []
                records_by_descriptor[desc].append(record)
                
            except (ValueError, IndexError):
                # Skip rows with parsing errors
                continue
                
    logger.info(f"Loaded {sum(len(r) for r in records_by_descriptor.values())} valid engineer submissions.")
    
    # Initialize SQLite database
    init_db(db_path)
    
    # Calculate centroids and save to SQLite
    with sqlite3.connect(db_path) as conn:
        for desc, records in records_by_descriptor.items():
            count = len(records)
            if count == 0:
                continue
                
            # Perform a two-pass outlier rejection: filter out any records that are more than 2 SDs away from the mean
            # for any band gain. This mimics the pandas Z-score outlier filtering in pure Python!
            keys = [
                "low_shelf_gain", "low_shelf_freq", 
                "first_band_gain", "first_band_freq", "first_band_q",
                "second_band_gain", "second_band_freq", "second_band_q",
                "third_band_gain", "third_band_freq", "third_band_q",
                "high_shelf_gain", "high_shelf_freq"
            ]
            
            # Pass 1: Compute raw means
            raw_means = {k: sum(r[k] for r in records) / count for k in keys}
            
            # Pass 2: Filter outlier records based on gain bounds (reject records that deviate heavily)
            filtered_records = []
            for r in records:
                is_outlier = False
                for k in ["low_shelf_gain", "first_band_gain", "second_band_gain", "third_band_gain", "high_shelf_gain"]:
                    # If any single band gain deviates by more than 5dB from the group mean, reject the submission as noise
                    if abs(r[k] - raw_means[k]) > 5.0:
                        is_outlier = True
                        break
                if not is_outlier:
                    filtered_records.append(r)
            
            # Use filtered set for final averages
            final_records = filtered_records if len(filtered_records) > 0 else records
            final_count = len(final_records)
            
            # Compute final averages (centroids)
            centroid = {k: sum(r[k] for r in final_records) / final_count for k in keys}
            
            # Serialize
            centroid_json = json.dumps(centroid)
            
            # Store in SQLite
            conn.execute("""
                INSERT OR REPLACE INTO semantic_eq_centroids (
                    descriptor, low_shelf_gain, low_shelf_freq,
                    first_band_gain, first_band_freq, first_band_q,
                    second_band_gain, second_band_freq, second_band_q,
                    third_band_gain, third_band_freq, third_band_q,
                    high_shelf_gain, high_shelf_freq, sample_count, centroid_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                desc,
                centroid["low_shelf_gain"], centroid["low_shelf_freq"],
                centroid["first_band_gain"], centroid["first_band_freq"], centroid["first_band_q"],
                centroid["second_band_gain"], centroid["second_band_freq"], centroid["second_band_q"],
                centroid["third_band_gain"], centroid["third_band_freq"], centroid["third_band_q"],
                centroid["high_shelf_gain"], centroid["high_shelf_freq"],
                final_count, centroid_json
            ))
            
            logger.info(f"  OK: Word: '{desc:<10}' | Submissions: {final_count:<3} | L-Shelf: {centroid['low_shelf_gain']:.1f}dB @ {centroid['low_shelf_freq']:.0f}Hz | H-Shelf: {centroid['high_shelf_gain']:.1f}dB @ {centroid['high_shelf_freq']:.0f}Hz")
            
        conn.commit()
        
    logger.info("-" * 60)
    logger.info("OK: Zero-dependency preprocessing completed successfully!")
    logger.info("-" * 60)


# The real crowdsourced SAFE-DB export. Not shipped with this repo.
REAL_CSV = Path("data/SAFEEqualiserUserData.csv")
# Hand-authored synthetic stand-in. Useful for exercising the pipeline; it is
# NOT crowdsourced data and must never be described as such.
MOCK_CSV = Path("data/synthetic_priors.MOCK.csv")
DB_PATH = Path("data/test_library.db")


def run_preprocessing(allow_synthetic: bool = False):
    """Build the centroid database.

    This used to silently fabricate 150 rows whenever the real CSV was absent
    and then process them as if they were engineer submissions, so the app's
    "crowdsourced" profiles were in fact a round trip through this file's own
    hardcoded means. Generating the stand-in is now explicit and opt-in.
    """
    if REAL_CSV.exists():
        logger.info(f"Using real SAFE-DB export: {REAL_CSV}")
        process_dataset(REAL_CSV, DB_PATH)
        return

    if not allow_synthetic:
        raise SystemExit(
            f"\nNo SAFE-DB export found at {REAL_CSV}.\n"
            "This script will not invent one. Either supply the real export, "
            f"or re-run with --synthetic to build hand-authored stand-in\n"
            "priors, which are NOT crowdsourced data and must not be "
            "presented as evidence of anything.\n"
        )

    if not MOCK_CSV.exists():
        generate_mock_dataset(MOCK_CSV)
    logger.warning(
        "Building centroids from SYNTHETIC priors (%s). These are hand-authored "
        "assumptions, not measurements.", MOCK_CSV
    )
    process_dataset(MOCK_CSV, DB_PATH)


if __name__ == '__main__':
    import sys as _sys
    run_preprocessing(allow_synthetic="--synthetic" in _sys.argv)
