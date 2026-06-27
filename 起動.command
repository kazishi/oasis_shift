#!/bin/bash
cd "$(dirname "$0")"

echo "========================================"
echo " OASIS Shift Tool"
echo "========================================"

# venv がなければ作成
if [ ! -f "venv/bin/activate" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create venv. Please install Python 3.12."
        read -p "Press Enter to close..."
        exit 1
    fi
fi

# venv を有効化
source venv/bin/activate

# パッケージをインストール
echo "Installing packages..."
pip install -r requirements.txt -q

echo "Starting Streamlit..."
streamlit run app.py
