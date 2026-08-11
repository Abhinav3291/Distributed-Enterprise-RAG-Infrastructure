import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from vector_engine import (
    ingest_pdf as extract_text,
    remove_pdf as delete_from_vector_db,
    update_pdf_location as update_file_in_vector_db,
    get_file_indexing_status
)

# Limit concurrent heavy PDF parsing tasks to prevent RAM spikes (OOM)
MAX_CONCURRENT_WORKERS = 4
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS)

# Set and Lock for tracking files currently being processed to prevent event spam
processing_files = set()
processing_lock = threading.Lock()


class RobustPDFHandler(FileSystemEventHandler):

    def _wait_for_file_write(self, file_path: str, retries: int = 15, delay: float = 0.5) -> bool:
        """
        Fixes EmptyFileError and race conditions by verifying:
        1. File exists on disk
        2. File size is strictly greater than 0 bytes
        3. File size has stopped changing (OS finished writing)
        4. File write lock is fully released
        """
        if not os.path.exists(file_path):
            return False

        last_size = -1
        for _ in range(retries):
            try:
                current_size = os.path.getsize(file_path)

                # Ensure file is not empty and size has stabilized
                if current_size > 0 and current_size == last_size:
                    # Attempt an append read-binary lock check to confirm OS release
                    with open(file_path, "a+b") as f:
                        pass
                    return True

                last_size = current_size
            except (PermissionError, OSError):
                # File is currently locked or being written to by the OS
                pass

            time.sleep(delay)

        return False

    def _submit_job(self, action_type: str, src_path: str, dest_path: str = None):
        """Deduplicates redundant OS events and offloads execution to worker pool."""
        norm_src = os.path.normpath(src_path)

        with processing_lock:
            if norm_src in processing_files:
                return  # Skip duplicate event if file is already being processed
            processing_files.add(norm_src)

        def worker_task():
            try:
                if action_type in ["created", "modified"]:
                    if self._wait_for_file_write(norm_src):
                        status = get_file_indexing_status(norm_src)
                        if status == "UNCHANGED":
                            print(f"[Watchdog] Content unchanged, skipping: {os.path.basename(norm_src)}")
                            return

                        if action_type == "modified" or status == "MODIFIED":
                            try:
                                delete_from_vector_db(norm_src)
                            except Exception as e:
                                print(f"[Watchdog Warning] Could not remove old vectors for {norm_src}: {e}")

                        print(f"[Watchdog] Processing ({status}): {os.path.basename(norm_src)}")
                        extract_text(norm_src)
                        print(f"[Watchdog Success] Fully ingested: {os.path.basename(norm_src)}")
                    else:
                        print(f"[Watchdog Warning] File remained empty or locked: {os.path.basename(norm_src)}")

                elif action_type == "deleted":
                    print(f"[Watchdog] Deleting vectors for: {os.path.basename(norm_src)}")
                    delete_from_vector_db(norm_src)

                elif action_type == "moved" and dest_path:
                    norm_dest = os.path.normpath(dest_path)
                    print(f"[Watchdog] Moving vectors: {os.path.basename(norm_src)} -> {os.path.basename(norm_dest)}")
                    update_file_in_vector_db(norm_src, norm_dest)

            except Exception as e:
                # Guarantees an exception never crashes Watchdog's Observer thread
                print(f"[Watchdog Error] Failed processing {os.path.basename(norm_src)}: {e}")
            finally:
                with processing_lock:
                    processing_files.discard(norm_src)

        executor.submit(worker_task)

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            self._submit_job("created", event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            self._submit_job("modified", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            self._submit_job("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            self._submit_job("moved", event.src_path, event.dest_path)


def start_watching(watch_dir: str):
    os.makedirs(watch_dir, exist_ok=True)

    print(f"[Watchdog] Scanning for existing PDFs in {watch_dir}...")
    for filename in os.listdir(watch_dir):
        if filename.lower().endswith('.pdf'):
            file_path = os.path.normpath(os.path.join(watch_dir, filename))

            status = get_file_indexing_status(file_path)
            if status == "UNCHANGED":
                print(f"[Watchdog] Skipping already indexed file: {filename}")
                continue

            print(f"[Watchdog] Enqueuing file for initial indexing ({status}): {filename}")

            def init_task(p=file_path):
                try:
                    extract_text(p)
                except Exception as e:
                    print(f"[Watchdog Error] Startup extraction failed for {p}: {e}")

            executor.submit(init_task)

    event_handler = RobustPDFHandler()
    observer = Observer()
    observer.schedule(event_handler, path=watch_dir, recursive=False)
    observer.start()

    print(f"[Watchdog] Observer started. Monitoring directory: {os.path.abspath(watch_dir)}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Watchdog] Shutting down observer and worker threads...")
        observer.stop()
        executor.shutdown(wait=False)

    observer.join()


if __name__ == "__main__":
    WATCH_DIRECTORY = "./Docx"
    start_watching(WATCH_DIRECTORY)