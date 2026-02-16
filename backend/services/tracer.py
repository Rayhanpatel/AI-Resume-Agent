"""
Langfuse Tracing Service (SDK v3)
Observability for LLM calls using OpenTelemetry-based SDK
Includes prompt management and cost tracking
"""
from langfuse import Langfuse
from typing import Optional, Any, Dict
import logging
import os

logger = logging.getLogger(__name__)

# Gemini 2.0 Flash pricing (per 1M tokens)
# Source: https://ai.google.dev/pricing
MODEL_PRICING = {
    "gemini-2.0-flash": {
        "input": 0.075,   # $0.075 per 1M input tokens
        "output": 0.30    # $0.30 per 1M output tokens
    },
    "gemini-1.5-flash": {
        "input": 0.075,
        "output": 0.30
    },
    "gemini-1.5-pro": {
        "input": 1.25,
        "output": 5.00
    }
}


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Calculate cost in USD for a generation"""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["gemini-2.0-flash"])
    
    input_cost = (tokens_in / 1_000_000) * pricing["input"]
    output_cost = (tokens_out / 1_000_000) * pricing["output"]
    
    return round(input_cost + output_cost, 6)


class TracingService:
    """Wrapper for Langfuse tracing with SDK v3"""
    
    def __init__(
        self, 
        public_key: str, 
        secret_key: str, 
        host: str = "https://cloud.langfuse.com"
    ):
        self._prompt_cache = {}
        
        # Skip initialization if keys are empty/missing
        if not public_key or not secret_key:
            logger.warning("Langfuse keys not provided - tracing disabled")
            self.client = None
            self.enabled = False
            return
        
        try:
            # Set environment variables for Langfuse SDK v3
            os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
            os.environ["LANGFUSE_SECRET_KEY"] = secret_key
            os.environ["LANGFUSE_BASE_URL"] = host
            
            # Get the Langfuse client
            self.client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host
            )
            self.enabled = True
            logger.info(f"Langfuse initialized with host: {host}")
        except Exception as e:
            logger.warning(f"Langfuse init failed: {e}")
            self.client = None
            self.enabled = False
    
    def get_prompt(
        self, 
        name: str, 
        fallback: str = None,
        cache_ttl: int = 300
    ) -> Optional[str]:
        """
        Fetch a prompt from Langfuse prompt management.
        
        Args:
            name: Name of the prompt in Langfuse
            fallback: Fallback prompt if fetch fails
            cache_ttl: Cache time in seconds (not used yet, for future)
        
        Returns:
            Prompt text or fallback
        """
        if not self.enabled:
            return fallback
        
        # Check cache first
        if name in self._prompt_cache:
            return self._prompt_cache[name]
        
        try:
            prompt = self.client.get_prompt(name)
            if prompt:
                compiled = prompt.compile()
                self._prompt_cache[name] = compiled
                logger.info(f"Loaded prompt '{name}' from Langfuse")
                return compiled
        except Exception as e:
            logger.warning(f"Failed to fetch prompt '{name}': {e}")
        
        return fallback
    
    def trace(
        self, 
        name: str, 
        session_id: str = None,
        user_id: Optional[str] = None,
        metadata: Optional[dict] = None
    ):
        """Create a new trace span - returns a dict-like object for compatibility"""
        if not self.enabled:
            return None
            
        # Include session_id in metadata since it's not a direct parameter in SDK v3
        full_metadata = metadata.copy() if metadata else {}
        if session_id:
            full_metadata["session_id"] = session_id
        if user_id:
            full_metadata["user_id"] = user_id
        
        # Create a simple trace info object to pass around
        return TraceInfo(name=name, session_id=session_id, metadata=full_metadata)
    
    def log_generation(
        self,
        trace,
        name: str,
        model: str,
        input_text: str,
        output_text: str,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        latency_ms: Optional[int] = None,
        metadata: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        Log an LLM generation with cost tracking.
        
        Returns dict with cost info for analytics.
        """
        result = {"logged": False, "cost_usd": 0.0}
        
        if not self.enabled:
            return result
            
        try:
            # Calculate cost if tokens available
            cost_usd = 0.0
            if tokens_in and tokens_out:
                cost_usd = calculate_cost(model, tokens_in, tokens_out)
            
            # Combine metadata
            full_metadata = metadata.copy() if metadata else {}
            if latency_ms:
                full_metadata["latency_ms"] = latency_ms
            if cost_usd:
                full_metadata["cost_usd"] = cost_usd
            if trace:
                full_metadata["trace_name"] = trace.name
                full_metadata["session_id"] = trace.session_id
            
            # Create generation observation
            with self.client.start_as_current_observation(
                as_type="generation",
                name=name,
                model=model,
                input=input_text,
                metadata=full_metadata
            ) as gen:
                gen.update(
                    output=output_text,
                    usage_details={
                        "prompt_tokens": tokens_in, 
                        "completion_tokens": tokens_out,
                        "total_tokens": (tokens_in or 0) + (tokens_out or 0)
                    } if tokens_in else None
                )
            
            result["logged"] = True
            result["cost_usd"] = cost_usd
            result["tokens_in"] = tokens_in
            result["tokens_out"] = tokens_out
            
            return result
            
        except Exception as e:
            logger.warning(f"Langfuse generation log failed: {e}")
            return result
    
    def log_span(
        self,
        name: str,
        input_text: str = None,
        output_text: str = None,
        metadata: Optional[dict] = None
    ):
        """Log a simple span"""
        if not self.enabled:
            return None
            
        try:
            with self.client.start_as_current_observation(
                as_type="span",
                name=name,
                input=input_text,
                metadata=metadata
            ) as span:
                if output_text:
                    span.update(output=output_text)
            return True
        except Exception as e:
            logger.warning(f"Langfuse span log failed: {e}")
            return None
    
    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str = None
    ):
        """Add a score to a trace for evaluation"""
        if not self.enabled:
            return None
        
        try:
            self.client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment
            )
            return True
        except Exception as e:
            logger.warning(f"Langfuse score failed: {e}")
            return None
    
    def flush(self):
        """Flush pending events"""
        if self.enabled and self.client:
            try:
                self.client.flush()
            except Exception as e:
                logger.warning(f"Langfuse flush failed: {e}")


class TraceInfo:
    """Simple trace info object for passing between functions"""
    def __init__(self, name: str, session_id: str = None, metadata: dict = None):
        self.name = name
        self.session_id = session_id
        self.metadata = metadata or {}
