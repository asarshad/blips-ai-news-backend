#!/usr/bin/env bash
#
# E2E Local Test Script
# 
# Runs a full end-to-end test of the backend locally:
# 1. Starts Postgres + Redis via docker-compose
# 2. Runs Alembic migrations
# 3. Seeds test content
# 4. Runs starter generation using FakeLLM
# 5. Validates API endpoints
#
# Usage:
#   ./scripts/e2e_local.sh
#
# Requirements:
#   - Docker and docker-compose
#   - Python 3.11+ with venv at .venv
#   - curl for API testing

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BACKEND_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Cleanup function
cleanup() {
    echo_info "Cleaning up..."
    if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
        kill "$API_PID" 2>/dev/null || true
    fi
    # Don't stop containers automatically - might want to inspect
    # docker-compose -f docker-compose.test.yml down
}
trap cleanup EXIT

# Check dependencies
check_deps() {
    echo_info "Checking dependencies..."
    
    command -v docker >/dev/null 2>&1 || { echo_error "docker is required but not installed."; exit 1; }
    command -v curl >/dev/null 2>&1 || { echo_error "curl is required but not installed."; exit 1; }
    
    if [ ! -d ".venv" ]; then
        echo_error "Python venv not found at .venv. Run: python -m venv .venv && pip install -r requirements.txt"
        exit 1
    fi
}

# Start infrastructure
start_infra() {
    echo_info "Starting Postgres + Redis via docker-compose..."
    docker-compose -f docker-compose.test.yml up -d
    
    echo_info "Waiting for services to be healthy..."
    local max_wait=30
    local waited=0
    
    while [ $waited -lt $max_wait ]; do
        if docker-compose -f docker-compose.test.yml ps | grep -q "healthy"; then
            # Check if both services are healthy
            local healthy_count
            healthy_count=$(docker-compose -f docker-compose.test.yml ps | grep -c "healthy" || true)
            if [ "$healthy_count" -ge 2 ]; then
                echo_info "All services healthy!"
                break
            fi
        fi
        sleep 1
        waited=$((waited + 1))
        echo -n "."
    done
    echo ""
    
    if [ $waited -ge $max_wait ]; then
        echo_error "Services failed to become healthy in ${max_wait}s"
        docker-compose -f docker-compose.test.yml logs
        exit 1
    fi
}

# Run migrations
run_migrations() {
    echo_info "Running Alembic migrations..."
    source .venv/bin/activate
    
    export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/blips_test"
    export REDIS_URL="redis://localhost:6380/0"
    export SKIP_STARTUP_CHECKS="true"
    export SCHEDULER_ENABLED="false"
    
    alembic upgrade head
    echo_info "Migrations complete"
}

# Seed test content
seed_content() {
    echo_info "Seeding test content..."
    source .venv/bin/activate
    
    export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/blips_test"
    export REDIS_URL="redis://localhost:6380/0"
    
    python -c "
from app.db.base import SessionLocal
from app.models.content import ContentItem, ContentType
from datetime import datetime

db = SessionLocal()

# Check if already seeded
existing = db.query(ContentItem).filter(ContentItem.source == 'E2E_TEST').first()
if existing:
    print('Test content already exists')
else:
    # Seed article
    article = ContentItem(
        type=ContentType.ARTICLE,
        source='E2E_TEST',
        source_url='https://test.example.com/e2e-test-article',
        title='E2E Test Article: AI Revolution in Software Development',
        description='Test article for E2E testing of conversation starters.',
        summary='This is a test summary about AI in software development.',
        published_at=datetime.utcnow(),
        topics=['AI', 'Software', 'Testing'],
        entities=[{'name': 'OpenAI', 'type': 'ORG'}],
        quality_score=0.8,
        global_score=0.75,
    )
    db.add(article)
    
    # Seed video
    video = ContentItem(
        type=ContentType.VIDEO,
        source='E2E_TEST',
        source_url='https://youtube.com/watch?v=e2e_test_video',
        title='E2E Test Video: Understanding Machine Learning Basics',
        description='Test video for E2E testing.',
        summary='A test video about machine learning fundamentals.',
        published_at=datetime.utcnow(),
        video_url='https://youtube.com/watch?v=e2e_test_video',
        duration_seconds=300,
        topics=['Machine Learning', 'AI', 'Tutorial'],
        entities=[],
        quality_score=0.7,
        global_score=0.65,
    )
    db.add(video)
    
    db.commit()
    print(f'Seeded article id={article.id} and video id={video.id}')

db.close()
"
    echo_info "Seeding complete"
}

# Start API server
start_api() {
    echo_info "Starting API server with FakeLLM..."
    source .venv/bin/activate
    
    export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/blips_test"
    export REDIS_URL="redis://localhost:6380/0"
    export LLM_PROVIDER="fake"
    export SCHEDULER_ENABLED="false"
    export SKIP_STARTUP_CHECKS="true"
    
    uvicorn app.main:app --host 0.0.0.0 --port 8001 &
    API_PID=$!
    
    echo_info "Waiting for API to be ready..."
    local max_wait=30
    local waited=0
    
    while [ $waited -lt $max_wait ]; do
        if curl -s http://localhost:8001/health >/dev/null 2>&1; then
            echo_info "API is ready!"
            break
        fi
        sleep 1
        waited=$((waited + 1))
        echo -n "."
    done
    echo ""
    
    if [ $waited -ge $max_wait ]; then
        echo_error "API failed to start in ${max_wait}s"
        exit 1
    fi
}

# Run API validation tests
validate_api() {
    echo_info "Validating API endpoints..."
    local base_url="http://localhost:8001/api/v1"
    local failed=0
    
    # Test 1: Get seeded article ID
    echo_info "Test 1: Finding seeded article..."
    local article_id
    article_id=$(curl -s "$base_url/articles/recent?limit=10" | python -c "
import sys, json
data = json.load(sys.stdin)
for item in data.get('articles', []):
    if 'E2E Test Article' in item.get('title', ''):
        print(item['id'])
        break
" 2>/dev/null || echo "")
    
    if [ -z "$article_id" ]; then
        echo_error "Failed to find seeded article"
        failed=1
    else
        echo_info "Found article id=$article_id"
    fi
    
    # Test 2: Get starters for article
    if [ -n "$article_id" ]; then
        echo_info "Test 2: Getting conversation starters for article..."
        local starters_response
        starters_response=$(curl -s "$base_url/starters/$article_id")
        
        if echo "$starters_response" | python -c "
import sys, json
data = json.load(sys.stdin)
starters = data.get('starters', [])
fallback = data.get('fallback', [])
if len(starters) >= 1 and len(fallback) >= 1:
    print('OK')
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
            echo_info "Starters generated successfully!"
            echo_info "Response: $starters_response"
        else
            echo_error "Starters response invalid: $starters_response"
            failed=1
        fi
    fi
    
    # Test 3: Verify starters were persisted
    if [ -n "$article_id" ]; then
        echo_info "Test 3: Verifying starters persisted in DB..."
        source .venv/bin/activate
        
        if python -c "
from app.db.base import SessionLocal
from app.models.content import ContentItem

db = SessionLocal()
item = db.query(ContentItem).filter(ContentItem.id == $article_id).first()
db.close()

if item and item.conversation_starters and len(item.conversation_starters.get('starters', [])) > 0:
    print('Persisted OK')
else:
    print('Not persisted')
    exit(1)
" 2>/dev/null; then
            echo_info "Starters persisted in database!"
        else
            echo_error "Starters not found in database"
            failed=1
        fi
    fi
    
    # Test 4: Check fallback starters exist
    echo_info "Test 4: Verifying fallback starters..."
    local fallback_count
    fallback_count=$(echo "$starters_response" | python -c "
import sys, json
data = json.load(sys.stdin)
print(len(data.get('fallback', [])))
" 2>/dev/null || echo "0")
    
    if [ "$fallback_count" -ge 1 ]; then
        echo_info "Fallback starters present ($fallback_count)"
    else
        echo_error "No fallback starters found"
        failed=1
    fi
    
    return $failed
}

# Main execution
main() {
    echo "============================================"
    echo "  E2E Local Test - Backend + Starters"
    echo "============================================"
    echo ""
    
    check_deps
    start_infra
    run_migrations
    seed_content
    start_api
    
    if validate_api; then
        echo ""
        echo "============================================"
        echo -e "  ${GREEN}E2E OK - All tests passed!${NC}"
        echo "============================================"
        exit 0
    else
        echo ""
        echo "============================================"
        echo -e "  ${RED}E2E FAILED - Some tests failed${NC}"
        echo "============================================"
        exit 1
    fi
}

main "$@"
