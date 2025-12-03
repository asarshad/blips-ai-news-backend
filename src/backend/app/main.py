
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import redis
import time

from app.core.config import settings
from app.api import api_router
from app.db.base import Base, engine
from app.scheduler import init_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create database tables
Base.metadata.create_all(bind=engine)

# Initialize Redis
redis_client = redis.from_url(settings.REDIS_URL)

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development only, restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Handle errors globally
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred"}
    )

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Add a health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}

# Initialize scheduler on startup
@app.on_event("startup")
def startup_event():
    logger.info("Starting up application")
    
    # Check Redis connection
    try:
        redis_client.ping()
        logger.info("Redis connection successful")
    except Exception as e:
        logger.error(f"Redis connection error: {str(e)}")
    
    # Start scheduler
    scheduler = init_scheduler()
    if scheduler:
        # Run initial news fetch (not waiting for scheduled time)
        from app.scheduler.tasks import fetch_and_process_news
        fetch_and_process_news()

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down application")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
