"""Unified LLM Client for EqualizerAI.

Wraps both Google Gemini API (via google-genai) and local OpenAI-compatible APIs
(like llama.cpp, Ollama, Llamafile) behind a unified, drop-in compatible interface.
"""

import json
import logging
import requests
from google import genai
from google.genai.types import GenerateContentConfig

logger = logging.getLogger(__name__)


class LLMResponse:
    """Wraps the response from the LLM to match google-genai response format."""
    def __init__(self, text: str):
        self.text = text


class LLMClient:
    """Unified LLM Client acting as a drop-in wrapper for genai.Client."""

    def __init__(self, config):
        """Initialize client based on config.yaml.

        Args:
            config: Config instance
        """
        self.config = config
        self.provider = config.llm_provider
        self.models = self  # Enables client.models.generate_content drop-in compatibility

        # Initialize Gemini Client if provider is gemini
        self.gemini_client = None
        if self.provider == 'gemini':
            api_key = config.gemini_api_key
            if not api_key:
                logger.warning("Gemini API key is not configured in config.yaml!")
            self.gemini_client = genai.Client(api_key=api_key)
            logger.info("LLMClient initialized in Google Gemini mode.")
        else:
            self.local_url = config.local_api_url
            self.local_model = config.local_model
            logger.info(f"LLMClient initialized in Local LLM mode ({self.local_model} at {self.local_url}).")

    def generate_content(
        self,
        model: str,
        contents: str,
        config: GenerateContentConfig = None
    ) -> LLMResponse:
        """Unified method to generate content, drop-in compatible with client.models.generate_content.

        Args:
            model: Gemini model name or local model name
            contents: Prompt string
            config: GenerateContentConfig object or dict

        Returns:
            LLMResponse object containing response text
        """
        # 1. Parse parameters from config if present
        temperature = 0.7
        response_mime_type = "text/plain"
        tools = None
        top_p = None

        if config is not None:
            # Check for attributes (GenerateContentConfig object)
            if hasattr(config, 'temperature') and config.temperature is not None:
                temperature = config.temperature
            elif isinstance(config, dict) and 'temperature' in config:
                temperature = config['temperature']

            if hasattr(config, 'response_mime_type') and config.response_mime_type is not None:
                response_mime_type = config.response_mime_type
            elif isinstance(config, dict) and 'response_mime_type' in config:
                response_mime_type = config['response_mime_type']

            if hasattr(config, 'tools') and config.tools is not None:
                tools = config.tools
            elif isinstance(config, dict) and 'tools' in config:
                tools = config['tools']

            if hasattr(config, 'top_p') and config.top_p is not None:
                top_p = config.top_p
            elif isinstance(config, dict) and 'top_p' in config:
                top_p = config['top_p']

        # 2. Delegate based on provider
        if self.provider == 'gemini':
            # Delegate to Gemini client
            config_args = {
                "temperature": temperature
            }
            if response_mime_type == "application/json":
                config_args["response_mime_type"] = "application/json"
            if tools:
                config_args["tools"] = tools
            if top_p is not None:
                config_args["top_p"] = top_p

            try:
                response = self.gemini_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=GenerateContentConfig(**config_args)
                )
                return LLMResponse(response.text)
            except Exception as e:
                logger.error(f"Gemini API call failed: {e}")
                raise e
        else:
            # Delegate to Local OpenAI-compatible API (llama-server or Ollama)
            headers = {
                "Content-Type": "application/json"
            }

            # Map the local model name from settings
            local_model_name = self.local_model or "local-model"

            # Convert prompt to standard messages format
            messages = [{"role": "user", "content": contents}]

            payload = {
                "model": local_model_name,
                "messages": messages,
                "temperature": temperature
            }
            if response_mime_type == "application/json":
                payload["response_format"] = {"type": "json_object"}
            if top_p is not None:
                payload["top_p"] = top_p

            try:
                # Append v1/chat/completions if not already in URL
                base_url = self.local_url.rstrip('/')
                if not base_url.endswith('/chat/completions') and not base_url.endswith('/completions'):
                    endpoint = f"{base_url}/chat/completions"
                else:
                    endpoint = base_url

                logger.debug(f"Sending request to Local LLM at {endpoint}...")
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=10  # Reduced timeout so fallback occurs quickly if server is offline
                )
                response.raise_for_status()
                data = response.json()
                generated_text = data['choices'][0]['message']['content']
                return LLMResponse(generated_text)
            except Exception as e:
                logger.error(f"Local LLM generation failed: {e}")
                
                # Dynamic self-healing fallback to Gemini
                api_key = self.config.gemini_api_key
                if api_key:
                    logger.warning("Local LLM down/unreachable. Activating automatic self-healing fallback to Gemini API...")
                    try:
                        if not self.gemini_client:
                            self.gemini_client = genai.Client(api_key=api_key)
                        
                        config_args = {
                            "temperature": temperature
                        }
                        if response_mime_type == "application/json":
                            config_args["response_mime_type"] = "application/json"
                        if tools:
                            config_args["tools"] = tools
                        if top_p is not None:
                            config_args["top_p"] = top_p
                            
                        # Use the configured gemini model name
                        fallback_model = self.config.gemini_model or "gemini-2.5-flash-lite"
                        
                        logger.info(f"Fallback querying Gemini API ({fallback_model})...")
                        response = self.gemini_client.models.generate_content(
                            model=fallback_model,
                            contents=contents,
                            config=GenerateContentConfig(**config_args)
                        )
                        logger.info("✓ Dynamic self-healing Gemini fallback successful!")
                        return LLMResponse(response.text)
                    except Exception as gemini_err:
                        logger.error(f"Gemini API fallback also failed: {gemini_err}")
                
                # Re-raise standard exception if no key or fallback failed
                raise RuntimeError(f"Local LLM call failed: {e}")
