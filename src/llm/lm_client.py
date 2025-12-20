import requests

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

def ask_llm(user_message):
    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": "Eres una IA empática, reflexiva y respetuosa. Responde de forma humana y clara."},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7
    }

    response = requests.post(LM_STUDIO_URL, json=payload)
    response.raise_for_status()

    return response.json()["choices"][0]["message"]["content"]
