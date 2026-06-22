#!/usr/bin/env python3
"""
Diagnostic script to check why files aren't appearing in the New Rx tab.

Usage:
    python diagnose_new_rx.py [optional: filename to search for]
    
Example:
    python diagnose_new_rx.py george
"""

import sys
import sqlite3
import os
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dmelogic.config import data_root
    from dmelogic.triage.service import new_rx_folder, intake_folder_name
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you're running this from the DMELogic project directory")
    sys.exit(1)

def diagnose_new_rx():
    """Diagnose New Rx file detection issues."""
    print("=" * 70)
    print("DMELogic New Rx Diagnostic Tool")
    print("=" * 70)
    
    # 1. Check intake folder configuration
    print("\n📁 INTAKE FOLDER CONFIGURATION:")
    try:
        intake_name = intake_folder_name()
        print(f"  • Folder name: {intake_name}")
    except Exception as e:
        print(f"  ❌ Error getting folder name: {e}")
        intake_name = "New Rx"
    
    # 2. Check actual folder path
    print("\n📂 ACTUAL FOLDER PATH:")
    try:
        rx_folder = new_rx_folder()
        print(f"  • Path: {rx_folder}")
        
        if rx_folder.exists():
            print(f"  ✅ Folder exists")
            # List files in folder
            files = list(rx_folder.iterdir())
            if files:
                print(f"  • {len(files)} file(s) found:")
                for f in files:
                    size_kb = f.stat().st_size / 1024
                    mod_time = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    print(f"    - {f.name} ({size_kb:.1f} KB, modified: {mod_time})")
                    
                    # Check if file is ignored
                    if f.name.startswith('.') or f.name.startswith('~$'):
                        print(f"      ⚠️  IGNORED: File starts with . or ~$ (hidden/temp)")
                    elif f.suffix.lower() in ['.db', '.db-wal', '.db-shm', '.tmp', '.part', '.crdownload']:
                        print(f"      ⚠️  IGNORED: File type is system/temporary")
                    elif f.suffix.lower() not in ['.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif']:
                        print(f"      ⚠️  IGNORED: File extension not supported")
                        print(f"         Supported: .pdf, .png, .jpg, .jpeg, .tif, .tiff, .bmp, .gif")
                    else:
                        print(f"      ✅ File type supported")
            else:
                print(f"  • ℹ️  Folder is empty")
        else:
            print(f"  ❌ Folder does NOT exist - TriageWidget auto-creates it")
            print(f"     Creating now...")
            try:
                rx_folder.mkdir(parents=True, exist_ok=True)
                print(f"  ✅ Created: {rx_folder}")
            except Exception as e:
                print(f"  ❌ Failed to create: {e}")
                
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return
    
    # 3. Check TriageStore database
    print("\n🗄️  TRIAGE STORE DATABASE:")
    search_term = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        from dmelogic.triage.store import TriageStore
        store = TriageStore(data_root())
        
        # Get all documents
        docs = store.get_all_documents()
        print(f"  • {len(docs)} document(s) in TriageStore")
        
        if docs:
            print("\n  Recent documents:")
            for doc in docs[-5:]:  # Last 5
                print(f"    - {doc['name']} (ID: {doc['doc_id']})")
                print(f"      Path: {doc['path']}")
        
        # Search for George's files if provided
        if search_term:
            print(f"\n  🔍 Searching for: '{search_term}'")
            george_docs = [d for d in docs if search_term.lower() in d['name'].lower()]
            if george_docs:
                print(f"  ✅ Found {len(george_docs)} matching document(s):")
                for doc in george_docs:
                    print(f"    - {doc['name']}")
                    print(f"      ID: {doc['doc_id']}, Path: {doc['path']}")
            else:
                print(f"  ❌ No documents matching '{search_term}' found in TriageStore")
                
    except Exception as e:
        print(f"  ❌ Error accessing TriageStore: {e}")
    
    # 4. Check file system for the search term
    if search_term and Path(rx_folder).exists():
        print(f"\n  📋 Direct filesystem search for '{search_term}':")
        matching_files = [f for f in Path(rx_folder).iterdir() if search_term.lower() in f.name.lower()]
        if matching_files:
            print(f"  ✅ Found {len(matching_files)} file(s):")
            for f in matching_files:
                print(f"    - {f.name}")
                # Check why it might not be in TriageStore
                if not f.is_file():
                    print(f"      ❌ Not a file (is directory?)")
                elif f.suffix.lower() not in ['.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif']:
                    print(f"      ⚠️  Unsupported file type: {f.suffix}")
                else:
                    print(f"      ✅ File should be detected - may need TriageWidget.refresh()")
        else:
            print(f"  ❌ No files matching '{search_term}' in {rx_folder}")
    
    # 5. Check data root configuration
    print(f"\n⚙️  DATA ROOT:")
    try:
        root = data_root()
        print(f"  • {root}")
        if Path(root).exists():
            print(f"  ✅ Exists")
        else:
            print(f"  ❌ Does NOT exist")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    print("\n" + "=" * 70)
    print("NEXT STEPS:")
    print("  1. Check file location matches the folder shown above")
    print("  2. Wait 5-10 seconds (auto-refresh interval)")
    print("  3. Click 'Refresh' button in New Rx tab")
    print("  4. If still missing, check file extension is supported")
    print("=" * 70)

if __name__ == "__main__":
    diagnose_new_rx()
