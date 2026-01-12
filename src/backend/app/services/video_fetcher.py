
"""Video fetcher service for retrieving videos from YouTube channels."""

from typing import List, Dict, Any, Optional

from app.core.logging import get_logger
from app.repositories.video_repo import VideoRepository
from app.integrations.youtube_client import YouTubeClient, VideoEntry
from app.integrations.openai_client import OpenAIClient

logger = get_logger(__name__)


class VideoFetcher:
    """Service for fetching videos from YouTube channels."""
    
    def __init__(
        self, 
        video_repo: VideoRepository,
        youtube_client: Optional[YouTubeClient] = None,
        openai_client: Optional[OpenAIClient] = None
    ):
        self.video_repo = video_repo
        self.youtube_client = youtube_client or YouTubeClient()
        self.openai_client = openai_client or OpenAIClient()

    def fetch_latest_videos(self) -> List[Dict[str, Any]]:
        """
        Fetch latest videos from YouTube channels, filtering out existing ones.
        
        Returns:
            List of video data dictionaries for new videos only
        """
        videos = []
        video_entries = self.youtube_client.fetch_all_channels(videos_per_channel=15)
        
        for entry in video_entries:
            if self.video_repo.get_by_url(entry.video_url):
                logger.debug(f"Video already exists: {entry.title}")
                continue
            
            # Summarize video description using AI
            try:
                summary = self.openai_client.summarize_video(entry.title, entry.summary)
                entry.summary = summary
            except Exception as e:
                logger.error(f"Failed to summarize video {entry.title}: {e}")
                # Fallback to original summary (already handled in summarize_video but good to be safe)
            
            video_data = self._entry_to_video_data(entry)
            videos.append(video_data)
            logger.info(f"Added new video: {entry.title}")
        
        logger.info(f"Total new videos: {len(videos)}")
        return videos
    
    def regenerate_summaries(self, limit: int = 50) -> int:
        """
        Regenerate summaries for existing videos using AI.
        
        Args:
            limit: Maximum number of videos to process
            
        Returns:
            Number of videos updated
        """
        videos = self.video_repo.get_recent(limit)
        updated_count = 0
        
        for video in videos:
            try:
                # Use current summary as description source
                current_text = video.summary
                
                # Check if we need to fetch transcript (if summary is default or missing)
                is_default_summary = not current_text or "Watch this video from" in current_text or len(current_text) < 50
                
                if is_default_summary:
                    # Try to fetch transcript
                    video_id = self.youtube_client._extract_video_id(video.video_url)
                    if video_id:
                        transcript = self.youtube_client.get_transcript(video_id)
                        if transcript:
                            current_text = transcript
                            logger.info(f"Fetched transcript for video: {video.title}")
                
                if not current_text:
                    continue

                # Skip if it looks like it's already processed (optional heuristic)
                # For now, we process everything to ensure consistency
                
                new_summary = self.openai_client.summarize_video(video.title, current_text)
                
                if new_summary and new_summary != video.summary:
                    self.video_repo.update_summary(video.id, new_summary)
                    updated_count += 1
                    logger.info(f"Regenerated summary for video: {video.title}")
                    
            except Exception as e:
                logger.error(f"Error regenerating summary for video {video.id}: {e}")
                continue
                
        return updated_count
    
    def _entry_to_video_data(self, entry: VideoEntry) -> Dict[str, Any]:
        """Convert a VideoEntry to video data dictionary."""
        # Try to get exact duration
        duration = self.youtube_client.get_video_duration(entry.video_id)
        
        # Check if it's a YouTube Short by URL pattern or title/description
        is_short = False
        if duration is None:
            # Fallback heuristic: Check if it's a Short using URL check
            is_short = self.youtube_client.is_youtube_short(entry.video_id)
            if not is_short:
                # Fallback to text heuristic - check for #shorts or short-form indicators
                text_lower = (entry.title + " " + entry.summary).lower()
                is_short = "#shorts" in text_lower or "#short" in text_lower
            
            # YouTube Shorts are typically under 60 seconds
            duration = 59 if is_short else None
        else:
            # Even with duration, classify as short if under 60 seconds
            is_short = duration < 60
        
        # Log shorts detection for debugging
        if is_short:
            logger.info(f"Detected YouTube Short: {entry.title} (duration: {duration}s)")

        return {
            "title": entry.title,
            "summary": entry.summary,
            "video_url": entry.video_url,
            "source_url": entry.video_url,
            "thumbnail_url": entry.thumbnail_url,
            "source": entry.source,
            "category": entry.category,
            "duration_seconds": duration,
            "is_short": is_short,
        }

    def save_videos(self, videos: List[Dict[str, Any]]) -> int:
        """
        Save fetched videos to database.
        
        Args:
            videos: List of video data dictionaries
            
        Returns:
            Number of videos successfully saved
        """
        saved_count = 0
        
        for video_data in videos:
            try:
                # Double-check existence to handle race conditions
                if self.video_repo.get_by_url(video_data["video_url"]):
                    continue
                
                db_video_data = {
                    "title": video_data["title"],
                    "summary": video_data["summary"],
                    "video_url": video_data["video_url"],
                    "source_url": video_data["source_url"],
                    "thumbnail_url": video_data.get("thumbnail_url"),
                    "source": video_data.get("source", "YouTube"),
                    "category": video_data.get("category", "Technology"),
                    "duration_seconds": video_data.get("duration_seconds"),
                }
                
                self.video_repo.create(db_video_data)
                saved_count += 1
                logger.info(f"Saved video: {video_data['title']}")
                
            except Exception as e:
                # Rollback the failed transaction so subsequent saves can work
                self.video_repo.db.rollback()
                logger.error(f"Error saving video '{video_data.get('title')}': {str(e)}")
        
        return saved_count
