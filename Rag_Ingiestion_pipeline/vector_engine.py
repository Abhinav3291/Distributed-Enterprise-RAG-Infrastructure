import os
import sys
import hashlib
import threading
import chromadb
from chromadb.utils import embedding_functions
from dotenv import find_dotenv, load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pymupdf4llm
from sentence_transformers import CrossEncoder
from openai import OpenAI

load_dotenv(find_dotenv())

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DB_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "data", "chroma_db"))

db_lock = threading.Lock()


def _calculate_file_hash(file_path: str) -> str:
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class VectorEngine:

    def __init__(self, collection_name: str = "rag_system"):
        self.collection_name = collection_name
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("[VectorEngine Warning] OPENAI_API_KEY not found in environment variables!")

        self.embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=api_key, model_name="text-embedding-3-small"
        )
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.character_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=150,
            separators=["\n\n", "\n", " "],
            length_function=len,
        )
        self._reranker = None

    @property
    def reranker(self) -> CrossEncoder:
        if self._reranker is None:
            print("[VectorEngine] Loading Cross-Encoder reranker (ms-marco-MiniLM-L-6-v2)...")
            self._reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        return self._reranker

    def _get_abs_path(self, file_path: str) -> str:
        return os.path.abspath(os.path.normpath(file_path))

    def get_collection(self) -> chromadb.Collection:
        return self.chroma_client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def get_file_indexing_status(self, file_path: str) -> str:
        abs_path = self._get_abs_path(file_path)
        if not os.path.exists(abs_path):
            return "NEW"

        try:
            current_hash = _calculate_file_hash(abs_path)
            with db_lock:
                collection = self.get_collection()
                results = collection.get(
                    where={"source": abs_path},
                    limit=1,
                    include=["metadatas"]
                )

            metadatas = results.get("metadatas", [])
            if not metadatas or len(metadatas) == 0:
                return "NEW"

            stored_hash = metadatas[0].get("file_hash", "")
            if stored_hash == current_hash:
                return "UNCHANGED"
            else:
                return "MODIFIED"

        except Exception as e:
            print(f"[VectorEngine Warning] Could not verify hash status for {file_path}: {e}")
            return "NEW"

    def _pdf_to_markdown(self, file_path: str) -> list[dict]:
        abs_path = self._get_abs_path(file_path)
        if not os.path.exists(abs_path) or not abs_path.lower().endswith(".pdf"):
            return []
        return pymupdf4llm.to_markdown(abs_path, page_chunks=True)

    def _chunk_pdf_content(
        self, page_content: list[dict], file_path: str
    ) -> tuple[list[str], list[dict], list[str]]:
        markdown_docs = []
        metadata = []
        ids = []

        abs_path = self._get_abs_path(file_path)
        filename = os.path.basename(abs_path)
        file_hash = _calculate_file_hash(abs_path)
        doc_hash = file_hash[:8]

        for idx, page_info in enumerate(page_content):
            text = page_info.get("text", "")
            page_num = idx + 1
            if len(text.strip()) < 20:
                continue

            sub_chunks = self.character_splitter.split_text(text)

            for sub_chunk_idx, sub_chunk_text in enumerate(sub_chunks):
                markdown_docs.append(sub_chunk_text)
                metadata.append(
                    {
                        "source": abs_path,
                        "filename": filename,
                        "page": page_num,
                        "file_hash": file_hash,
                    }
                )
                ids.append(f"doc_{doc_hash}_p{page_num}_c{sub_chunk_idx}")

        return markdown_docs, metadata, ids

    def ingest_pdf(self, file_path: str) -> None:
        abs_path = self._get_abs_path(file_path)
        pages = self._pdf_to_markdown(abs_path)
        if not pages:
            print(f"[Ingest Warning] No content extracted from: {abs_path}")
            return

        docs, metadatas, ids = self._chunk_pdf_content(pages, abs_path)
        if not docs:
            return

        with db_lock:
            collection = self.get_collection()
            collection.delete(where={"source": abs_path})
            collection.upsert(documents=docs, metadatas=metadatas, ids=ids)

        print(f"[Ingest Success] Embedded {len(docs)} chunks for: {os.path.basename(abs_path)}")

    def remove_pdf(self, file_path: str) -> None:
        try:
            abs_path = self._get_abs_path(file_path)
            with db_lock:
                collection = self.get_collection()
                collection.delete(where={"source": abs_path})
            print(f"[Ingest] Deleted vectors for: {os.path.basename(abs_path)}")
        except Exception as e:
            print(f"[Ingest Error] Failed to delete vectors for {file_path}: {e}")

    def update_pdf_location(self, old_path: str, new_path: str) -> None:
        self.remove_pdf(old_path)
        if os.path.exists(new_path) and new_path.lower().endswith(".pdf"):
            self.ingest_pdf(new_path)

    def search_and_rerank(
        self,
        query: str,
        top_k: int = 10,
        top_n: int = 3,
        min_score: float = -1.0,
    ) -> list[dict]:
        collection = self.get_collection()
        results = collection.query(query_texts=[query], n_results=top_k)

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        ids = results.get("ids", [[]])[0]

        if not documents:
            return []

        candidates = [
            {
                "id": ids[i],
                "text": documents[i],
                "metadata": metadatas[i] if i < len(metadatas) else {},
            }
            for i in range(len(documents))
        ]

        pairs = [[query, doc["text"]] for doc in candidates]
        scores = self.reranker.predict(pairs)

        valid_chunks = []
        for i, score in enumerate(scores):
            score_val = float(score)
            if score_val >= min_score:
                candidates[i]["rerank_score"] = score_val
                valid_chunks.append(candidates[i])

        valid_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)
        return valid_chunks[:top_n]

    def query(
        self,
        query: str,
        top_k: int = 10,
        top_n: int = 3,
        min_score: float = -1.0,
    ) -> dict:
        context_chunks = self.search_and_rerank(
            query=query, top_k=top_k, top_n=top_n, min_score=min_score
        )

        if not context_chunks:
            return {
                "answer": "I could not find any relevant information in the knowledge base to answer your query.",
                "context_chunks": [],
            }

        context_text = "\n\n".join(
            [
                f"Source: {c['metadata'].get('filename', 'unknown')} (Page {c['metadata'].get('page', '1')})\n{c['text']}"
                for c in context_chunks
            ]
        )

        prompt = f"""You are an enterprise AI assistant. Answer the user's query accurately using ONLY the provided context below. If the answer cannot be found in the context, state that you do not know.

Context:
{context_text}

User Query: {query}
Answer:"""

        try:
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            llm_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

            response = client.chat.completions.create(
                model=llm_model,
                messages=[
                    {"role": "system", "content": "You are a precise enterprise RAG assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=800,
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = f"Error generating LLM response: {str(e)}"

        return {
            "answer": answer,
            "context_chunks": context_chunks,
        }


_engine = VectorEngine()

def ingest_pdf(file_path: str) -> None:
    _engine.ingest_pdf(file_path)

def remove_pdf(file_path: str) -> None:
    _engine.remove_pdf(file_path)

def update_pdf_location(old_path: str, new_path: str) -> None:
    _engine.update_pdf_location(old_path, new_path)

def get_file_indexing_status(file_path: str) -> str:
    return _engine.get_file_indexing_status(file_path)