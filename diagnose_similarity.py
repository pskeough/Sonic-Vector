import sys
import os
from pathlib import Path

# Add script directory to Path
SCRIPT_DIR = Path(__file__).parent.absolute()
sys.path.insert(0, str(SCRIPT_DIR))

from lastfm_client import LastFMClient
from embed_song_predictor import SemanticEQPredictor

def run_diag():
    client = LastFMClient(config_path="config.yaml")
    predictor = SemanticEQPredictor(db_path=SCRIPT_DIR / "data/test_library.db")
    
    artist = "Richy Mitch & The Coal Miners"
    track = "Lucerne"
    
    print("\n==================================================")
    print(f"DIAGNOSING SIMILARITY GRAB FOR '{track}' BY '{artist}'")
    print("==================================================")
    
    # 1. Harvest Tags
    tags = client.get_track_tags(artist, track)
    print(f"\n[OK] Harvested Tags ({len(tags)}):")
    print(tags)
    
    # 2. Compute similarity weights
    weights = predictor.calculate_similarity_weights(tags)
    print("\n[OK] Calculated Vector Similarity Weights:")
    for profile, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {profile:<10} : {w*100:5.1f}%")
        
    # 3. Synthesize EQ bands
    eq = predictor.synthesize_eq_curve(weights)
    print("\n[OK] Synthesized Parametric EQ Curve:")
    print(f"  - Low Shelf  : {eq['low_shelf_gain']:+5.2f} dB @ {eq['low_shelf_freq']:.0f} Hz")
    print(f"  - PK Band 1  : {eq['first_band_gain']:+5.2f} dB @ {eq['first_band_freq']:.0f} Hz (Q={eq['first_band_q']:.2f})")
    print(f"  - PK Band 2  : {eq['second_band_gain']:+5.2f} dB @ {eq['second_band_freq']:.0f} Hz (Q={eq['second_band_q']:.2f})")
    print(f"  - PK Band 3  : {eq['third_band_gain']:+5.2f} dB @ {eq['third_band_freq']:.0f} Hz (Q={eq['third_band_q']:.2f})")
    print(f"  - High Shelf : {eq['high_shelf_gain']:+5.2f} dB @ {eq['high_shelf_freq']:.0f} Hz")
    print("==================================================\n")

if __name__ == '__main__':
    run_diag()
