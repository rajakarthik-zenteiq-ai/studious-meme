#!/usr/bin/env python3
"""
Run the enhanced MCP Streamlit UI
This script starts the improved UI with all the fixes
"""
import subprocess
import sys
import os
from pathlib import Path

def check_requirements():
    """Check if all required packages are installed"""
    required_packages = [
        "streamlit",
        "asyncio",
        "httpx",
        "motor",
        "pymongo",
        "minio"
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"❌ Missing packages: {', '.join(missing)}")
        print("Please install them with: pip install " + " ".join(missing))
        return False
    
    return True

def main():
    """Run the enhanced UI"""
    print("🚀 Starting Enhanced MCP Agent UI")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not Path("ui_streamlit.py").exists():
        print("❌ Please run this script from the MCP project root directory")
        sys.exit(1)
    
    # Check requirements
    if not check_requirements():
        sys.exit(1)
    
    print("✅ All requirements satisfied")
    
    # Set environment variables for better performance
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_SERVER_ENABLE_CORS"] = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] = "false"
    
    try:
        print("🌐 Starting Streamlit server...")
        print("📱 UI will be available at: http://localhost:8501")
        print("🔧 Features included:")
        print("   - Enhanced error handling")
        print("   - File upload with progress tracking")
        print("   - Tool call monitoring")
        print("   - Role-based access control")
        print("   - Database file management")
        print("   - Metadata sync tools")
        print("\n" + "=" * 50)
        
        # Run streamlit
        subprocess.run([
            sys.executable, "-m", "streamlit", "run", 
            "ui_streamlit.py",
            "--server.port", "8501",
            "--server.address", "0.0.0.0",
            "--theme.base", "light"
        ])
        
    except KeyboardInterrupt:
        print("\n🛑 UI stopped by user")
    except Exception as e:
        print(f"❌ Error starting UI: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
