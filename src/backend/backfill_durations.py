import sys
import os
import re
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add current directory to path so we can import app
sys.path.append(os.getcwd())

from app.db.base import Base
from app.models.video import Video
from app.core.config import settings

# Setup DB
# We need to use the internal Docker URL if running inside container
# But settings.DATABASE_URL should be correct from env vars
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

def get_video_duration(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            match = re.search(r'"approxDurationMs":"(\d+)"', response.text)
            if match:
                ms = int(match.group(1))
                return ms // 1000
    except Exception as e:
        print(f"Error fetching duration for {video_id}: {e}")
    return None

def backfill():
    print("Starting backfill...")
    # Find videos with null duration
    videos = db.query(Video).filter(Video.duration_seconds == None).all()
    print(f"Found {len(videos)} videos without duration")
    
    count = 0
    for video in videos:
        # Extract video ID from URL
        # URL format: https://www.youtube.com/watch?v=VIDEO_ID
        try:
            if "v=" in video.video_url:
                video_id = video.video_url.split("v=")[-1].split("&")[0]
            else:
                # Handle shorts URL if present: youtube.com/shorts/ID
                video_id = video.video_url.split("/")[-1]
            
            duration = get_video_duration(video_id)
            
            if duration:
                video.duration_seconds = duration
                print(f"Updated '{video.title}': {duration}s")
                count += 1
                
                # Commit every 5 to avoid losing progress
                if count % 5 == 0:
                    db.commit()
            else:
                print(f"Could not get duration for '{video.title}' (ID: {video_id})")
                
        except Exception as e:
            print(f"Error processing {video.video_url}: {e}")
            
    db.commit()
    print(f"Done. Updated {count} videos.")

if __name__ == "__main__":
    backfill()
