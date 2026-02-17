#!/bin/bash
# Quick setup script for Polymarket bot

echo "========================================="
echo "Polymarket Bot - Phase 1 Setup"
echo "========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install requirements
echo ""
echo "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Copy environment file
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file..."
    cp .env.example .env
    echo "✓ .env created - please edit with your configuration"
else
    echo ""
    echo "✓ .env already exists"
fi

# Create directories
echo ""
echo "Creating directories..."
mkdir -p data logs

# Initialize database
echo ""
echo "Initializing database..."
python scripts/manage.py init

echo ""
echo "========================================="
echo "✓ Setup complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env file with your configuration (optional for Phase 1)"
echo "  2. Test the scrapers:"
echo "     python scripts/manage.py test-polymarket"
echo "     python scripts/manage.py test-scraper"
echo "  3. Run data collection:"
echo "     python scripts/manage.py collect"
echo "  4. Start scheduled collection:"
echo "     python scripts/manage.py schedule --auto-trade"
echo ""
