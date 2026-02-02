import os
import shutil
import uuid
import zipfile
from datetime import datetime
from fastapi import UploadFile, HTTPException
from typing import List, Tuple

WORKSPACES_DIR = os.path.abspath("workspaces")

import json

class ProjectService:
    @staticmethod
    async def create_project(file: UploadFile, project_name: str = None) -> Tuple[str, str, int]:
        project_id = f"proj_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        project_dir = os.path.join(WORKSPACES_DIR, project_id)
        os.makedirs(project_dir, exist_ok=True)

        if not project_name:
            project_name = file.filename.split('.')[0]

        file_location = os.path.join(project_dir, file.filename)
        
        # Save uploaded file
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_count = 0
        if file.filename.endswith(".zip"):
            with zipfile.ZipFile(file_location, 'r') as zip_ref:
                zip_ref.extractall(project_dir)
            # Remove the zip file after extraction
            os.remove(file_location)
            
            # Count files
            for root, dirs, files in os.walk(project_dir):
                for f in files:
                    if f.endswith(('.c', '.h')):
                        file_count += 1
        else:
            # Single file
            if file.filename.endswith(('.c', '.h')):
                file_count = 1
            else:
                # Should we reject? For now allow but count is 0 if not c/h
                pass

        # Save metadata
        meta = {
            "project_name": project_name,
            "created_at": datetime.now().isoformat(),
            "original_filename": file.filename
        }
        with open(os.path.join(project_dir, "meta.json"), "w") as f:
            json.dump(meta, f)

        return project_id, project_name, file_count

    @staticmethod
    def get_project_path(project_id: str) -> str:
        path = os.path.join(WORKSPACES_DIR, project_id)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Project not found")
        return path

    @staticmethod
    def list_files(project_id: str) -> List[str]:
        project_dir = ProjectService.get_project_path(project_id)
        file_paths = []
        for root, _, files in os.walk(project_dir):
            for file in files:
                if file.endswith(('.c', '.h')):
                    # Return relative path from project root
                    rel_path = os.path.relpath(os.path.join(root, file), project_dir)
                    file_paths.append(rel_path)
        return sorted(file_paths)

    @staticmethod
    def get_file_content(project_id: str, file_path: str) -> str:
        project_dir = ProjectService.get_project_path(project_id)
        # Prevent directory traversal
        full_path = os.path.abspath(os.path.join(project_dir, file_path))
        if not full_path.startswith(project_dir):
             raise HTTPException(status_code=400, detail="Invalid file path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")
            
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    @staticmethod
    def get_project_name(project_id: str) -> str:
        project_dir = os.path.join(WORKSPACES_DIR, project_id)
        meta_path = os.path.join(project_dir, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                    return meta.get("project_name", project_id)
            except:
                pass
        
        # Fallback for old projects: try to find a meaningful name
        try:
            items = os.listdir(project_dir)
            # 1. If there's a single directory, use it
            dirs = [d for d in items if os.path.isdir(os.path.join(project_dir, d)) and not d.startswith(('_', '.'))]
            if len(dirs) == 1:
                return dirs[0]
            
            # 2. Look for any .c file and use its parent directory name if it's not the project root
            for root, _, files in os.walk(project_dir):
                c_files = [f for f in files if f.endswith('.c')]
                if c_files:
                    rel_dir = os.path.relpath(root, project_dir)
                    if rel_dir != '.':
                        return rel_dir.split(os.sep)[0]
                    return c_files[0].split('.')[0]
        except:
            pass

        # 3. Last resort: use the random part of the ID instead of the date
        parts = project_id.split('_')
        return parts[-1] if len(parts) > 1 else project_id

    @staticmethod
    def delete_project(project_id: str) -> bool:
        # Debug logging
        print(f"DEBUG: Attempting to delete project_id: {repr(project_id)}")
        
        # Clean up project_id just in case (strip whitespace/newlines)
        project_id = project_id.strip()
        
        project_dir = os.path.join(WORKSPACES_DIR, project_id)
        print(f"DEBUG: Target directory: {project_dir}, Exists: {os.path.exists(project_dir)}")
        
        if not os.path.exists(project_dir):
            return False
        try:
            shutil.rmtree(project_dir)
            return True
        except Exception as e:
            print(f"Error deleting project {project_id}: {e}")
            return False

    @staticmethod
    def list_projects() -> List[dict]:
        if not os.path.exists(WORKSPACES_DIR):
            return []
        
        projects = []
        for d in os.listdir(WORKSPACES_DIR):
            path = os.path.join(WORKSPACES_DIR, d)
            if os.path.isdir(path) and d.startswith("proj_"):
                # Count files
                file_count = 0
                for root, _, files in os.walk(path):
                    for f in files:
                        if f.endswith(('.c', '.h')):
                            file_count += 1
                
                # Get project name from meta
                project_name = ProjectService.get_project_name(d)
                
                projects.append({
                    "project_id": d,
                    "project_name": project_name,
                    "file_count": file_count,
                    "status": "available"
                })
        return sorted(projects, key=lambda x: x['project_id'], reverse=True)
