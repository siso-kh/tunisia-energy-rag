import streamlit as st
import requests
import os

# Configure page
st.set_page_config(page_title="Tunisia Energy RAG", page_icon="⚡", layout="centered")
st.title("⚡ Tunisia Energy Intelligence")
st.markdown("Ask questions about the Tunisian energy transition, regulations, and infrastructure.")

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/api/chat")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

def render_sources(sources):
    """Helper function to render structured sources consistently."""
    if sources:
        with st.expander("🔍 View Retrieved Sources"):
            for idx, source in enumerate(sources, 1):
                file_name = source.get("source_name", "Unknown Document")
                page_num = source.get("page", "N/A")
                content = source.get("content", "")
                
                st.markdown(f"**Source {idx}:** `{file_name}` | **Page:** `{page_num}`")
                st.info(content)
                if idx < len(sources):
                    st.divider()

# 1. Render historical chat messages on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and "sources" in message:
            render_sources(message["sources"])

# 2. React to user input
if prompt := st.chat_input("Posez votre question ici..."):
    clean_prompt = prompt.strip()
    
    if not clean_prompt:
        st.warning("Veuillez saisir une question valide.")
        st.stop()
        
    # Render user prompt immediately
    st.chat_message("user").markdown(clean_prompt)
    
    # Prepare payload with past conversation history for the backend
    payload_history = [
        {"role": m["role"], "content": m["content"]} 
        for m in st.session_state.messages
    ]
    
    # Append user prompt to state
    st.session_state.messages.append({"role": "user", "content": clean_prompt})

    # Call FastAPI backend
    with st.chat_message("assistant"):
        with st.spinner("Recherche et synthèse en cours..."):
            try:
                payload = {
                    "query": clean_prompt,
                    "chat_history": payload_history
                }
                response = requests.post(API_URL, json=payload, timeout=30)
                response.raise_for_status()
                
                data = response.json()
                answer = data.get("answer", "Erreur: Pas de réponse générée.")
                sources = data.get("sources", [])
                
                # Render answer once
                st.markdown(answer)
                
                # Render structured sources expander
                render_sources(sources)
                
                # Save assistant turn to session state
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": answer,
                    "sources": sources
                })
                
            except requests.exceptions.RequestException as e:
                error_msg = f"API Connection Error: {e}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})