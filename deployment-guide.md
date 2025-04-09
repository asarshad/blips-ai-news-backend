
# Blips AI News - Production Deployment Guide

This guide provides instructions for deploying the Blips AI News backend to a production environment.

## Prerequisites

- A Linux server (Ubuntu 20.04 LTS or newer recommended)
- Docker and Docker Compose installed
- Git installed
- Domain name (optional but recommended)
- OpenAI API key

## Step 1: Clone the Repository

```bash
git clone <your-repository-url>
cd <repository-directory>
```

## Step 2: Configure Environment Variables

Create a `.env` file in the root directory:

```bash
# OpenAI
OPENAI_API_KEY=your-openai-api-key-here

# Optional: Override default settings
# NEWS_FETCH_INTERVAL_HOURS=3
# MAX_MESSAGES_PER_DAY=5
# MAX_MESSAGES_PER_ARTICLE=3
```

## Step 3: Setup Production Security (Important!)

For a production environment, update the database passwords in `src/docker-compose.yml`:

```yaml
- POSTGRES_USER=secure_username
- POSTGRES_PASSWORD=secure_password
- DATABASE_URL=postgresql://secure_username:secure_password@db:5432/blips
```

## Step 4: Deploy with Docker Compose

Navigate to the `src` directory and start the services:

```bash
cd src
docker-compose up -d
```

This will start three containers:
- FastAPI backend application
- PostgreSQL database
- Redis cache

## Step 5: Apply Database Migrations

Run migrations to set up the database schema:

```bash
docker exec -it src-api-1 alembic upgrade head
```

## Step 6: Set Up a Reverse Proxy (Optional but Recommended)

For production, it's recommended to set up Nginx as a reverse proxy:

1. Install Nginx:
   ```bash
   sudo apt update
   sudo apt install nginx
   ```

2. Create an Nginx configuration file:
   ```bash
   sudo nano /etc/nginx/sites-available/blips
   ```

3. Add the following configuration (replace `yourdomain.com` with your actual domain):
   ```
   server {
       listen 80;
       server_name yourdomain.com;

       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

4. Enable the site and restart Nginx:
   ```bash
   sudo ln -s /etc/nginx/sites-available/blips /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

5. Set up SSL with Certbot (recommended):
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d yourdomain.com
   ```

## Step 7: Test and Monitor

Check that the application is running:

```bash
curl http://localhost:8000/health
```

Monitor logs:

```bash
docker-compose logs -f api
```

## Step 8: Setup Regular Backups

Set up a cron job to backup the database:

```bash
crontab -e
```

Add this line to backup daily at 2 AM:

```
0 2 * * * docker exec src-db-1 pg_dump -U postgres blips > /path/to/backups/blips_$(date +\%Y\%m\%d).sql
```

## Maintenance

### Updating the Application

To update the application:

```bash
git pull
docker-compose down
docker-compose up --build -d
```

### Scaling

For higher load, consider:

1. Increasing Gunicorn workers in the Dockerfile
2. Setting up database replication
3. Implementing a load balancer for multiple API instances

## Troubleshooting

- **Database connection issues**: Check PostgreSQL logs with `docker-compose logs db`
- **API errors**: Check API logs with `docker-compose logs api`
- **Redis issues**: Check Redis logs with `docker-compose logs redis`

## Frontend Integration

To connect the frontend to this backend, update the API base URL in your frontend configuration to point to your production API endpoint.
