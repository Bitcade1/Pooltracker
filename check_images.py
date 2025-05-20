import os
import sys

def check_images():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "images")
    
    required_images = [
        "Black Oak.jpg",
        "Rustic Oak.jpg",
        "Grey Oak.jpg",
        "Stone.jpg"
    ]
    
    print(f"Checking images in: {images_dir}")
    
    if not os.path.exists(images_dir):
        print(f"ERROR: Images directory not found: {images_dir}")
        return False
        
    all_good = True
    for image in required_images:
        path = os.path.join(images_dir, image)
        if os.path.exists(path):
            print(f"✓ Found: {image}")
        else:
            print(f"✗ Missing: {image}")
            all_good = False
            
    return all_good

if __name__ == "__main__":
    if check_images():
        print("\nAll images found successfully!")
    else:
        print("\nSome images are missing. Please check the images folder.")
        sys.exit(1)
