from your_app import db  # Replace 'your_app' with the actual name of your app module
from your_app.models import WoodCount  # Replace 'models' with the module where `WoodCount` is defined

def drop_table():
    try:
        print("Attempting to drop the WoodCount table...")
        WoodCount.__table__.drop(db.engine)
        print("WoodCount table dropped successfully.")
    except Exception as e:
        print(f"Error dropping WoodCount table: {e}")

if __name__ == "__main__":
    drop_table()
