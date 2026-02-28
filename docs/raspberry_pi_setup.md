## Hardware

- **Device:** Raspberry Pi Zero 2 W

## 1. Install Dependencies on PI
```bash
sudo apt update
sudo apt upgrade -y

sudo apt install python3-pip python3-venv git -y

git clone git@github.com:Ejay234/GeoPulse.git
cd GeoPulse

python3 -m venv venv
source venv/bin/source
pip install --upgrade pip
pip install -r requirement.txt
```

## 2. Authenticate GEE on Pi
```bash
python -c "import ee; ee.Authenticate()"

python -c "import ee; ee.Initialize(project='id'); print('GEE connected')"
```

## 3. Run GeoPulse Server
```bash
python scripts/scoring.py
python scripts/visualize.py

python app.py
```
