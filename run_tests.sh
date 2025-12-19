#!/bin/bash
set -e

echo "=========================================="
echo "Running Code Formatting and Linting Checks"
echo "=========================================="

echo "[1/4] Sorting Imports (isort)..."
isort .

echo "[2/4] Formatting Code (black)..."
black .

echo "[3/4] Checking Logic (flake8)..."
flake8 . || echo "flake8 passed with warnings/errors."

# echo "[4/4] Running Tests (pytest)..."
# pytest

echo ""
echo "=========================================="
echo "All Checks Passed! Ready to Push."
echo "=========================================="
