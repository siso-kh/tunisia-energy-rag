#!/bin/bash
# Tunisia Energy RAG - Quick Setup Script
# Run this script to set up and start the application

set -e

echo "=========================================="
echo "  Tunisia Energy RAG - Setup Script"
echo "=========================================="

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    echo "   Visit: https://docs.docker.com/compose/install/"
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cat > .env << 'EOF'
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/energie_tunisie

# LLM API Key (required)
CUSTOM_API_KEY=your_api_key_here

# Security (required for production)
JWT_SECRET=your_jwt_secret_here
ADMIN_API_KEY=your_admin_api_key_here

# Rate Limiting
RATE_LIMIT_CHAT=10/minute
RATE_LIMIT_AUTH=10/minute
RATE_LIMIT_STORAGE_URI=memory://

# Security (P0 fixes)
TRUST_PROXY_HEADERS=false

# CORS
CORS_ORIGINS=*

# Ngrok (optional)
# NGROK_AUTHTOKEN=your_token_here
EOF
    echo "✅ Created .env file"
    echo "⚠️  Please edit .env and add your API keys before continuing"
    echo ""
    read -p "Press Enter after editing .env..."
fi

# Build and start services
echo ""
echo "🔨 Building Docker containers..."
docker compose build

echo ""
echo "🚀 Starting services..."
docker compose up -d

# Wait for services to be healthy
echo ""
echo "⏳ Waiting for services to start..."
sleep 10

# Check health
echo ""
echo "🏥 Checking service health..."
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Backend is healthy"
else
    echo "⚠️  Backend is starting up (may take 30-60 seconds)"
fi

if curl -s http://localhost > /dev/null 2>&1; then
    echo "✅ Frontend is running"
else
    echo "⚠️  Frontend is starting up"
fi

echo ""
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "📱 Frontend: http://localhost"
echo "🔧 Backend API: http://localhost:8000"
echo "📊 Dashboard: http://localhost:8000/dashboard"
echo "🔑 Admin: http://localhost/admin"
echo ""
echo "📋 Useful commands:"
echo "   docker compose logs -f        # View logs"
echo "   docker compose down           # Stop services"
echo "   docker compose up -d          # Start services"
echo "   docker compose restart        # Restart services"
echo ""
