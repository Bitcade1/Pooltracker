from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import os

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Configure database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'pool_table_tracker.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# Define the WoodCount model
class WoodCount(db.Model):
    __tablename__ = 'wood_count'
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(50), nullable=False)
    count = db.Column(db.Integer, default=0, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    time = db.Column(db.Time, default=datetime.utcnow().time, nullable=False)

# Drop the WoodCount table
def drop_table():
    with app.app_context():
        try:
            print("Attempting to drop the WoodCount table...")
            WoodCount.__table__.drop(db.engine)
            print("WoodCount table dropped successfully.")
        except Exception as e:
            print(f"Error dropping WoodCount table: {e}")

if __name__ == "__main__":
    drop_table()
