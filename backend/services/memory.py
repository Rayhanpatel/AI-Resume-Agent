"""
Mem0 Memory Service
Handles conversation memory with semantic search and fact extraction
"""
from mem0 import AsyncMemoryClient
from typing import List, Optional, Dict, Any
import logging
import re
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class MemoryService:
    """Wrapper for Mem0 memory operations with semantic search and fact extraction"""
    
    def __init__(self, api_key: str):
        try:
            self.client = AsyncMemoryClient(api_key=api_key)
            self.enabled = True
            logger.info("Mem0 client initialized")
        except Exception as e:
            logger.warning(f"Mem0 init failed: {e}")
            self.client = None
            self.enabled = False
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def add_message(
        self, 
        session_id: str, 
        role: str, 
        content: str,
        metadata: Optional[dict] = None
    ) -> bool:
        """Store a message in memory"""
        if not self.enabled:
            return False
            
        try:
            await self.client.add(
                messages=[{"role": role, "content": content}],
                user_id=session_id,
                metadata=metadata or {}
            )
            return True
        except Exception as e:
            logger.warning(f"Mem0 add failed: {e}")
            return False
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def add_facts(
        self,
        session_id: str,
        facts: List[str],
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Store extracted facts about the user.
        
        Args:
            session_id: Session identifier
            facts: List of fact strings to store
            metadata: Optional metadata
        
        Returns:
            Success status
        """
        if not self.enabled or not facts:
            return False
        
        try:
            # Store facts as a structured message
            facts_message = "User facts: " + "; ".join(facts)
            await self.client.add(
                messages=[{"role": "assistant", "content": facts_message}],
                user_id=session_id,
                metadata={"type": "facts", **(metadata or {})}
            )
            return True
        except Exception as e:
            logger.warning(f"Mem0 add_facts failed: {e}")
            return False
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def get_history(
        self, 
        session_id: str, 
        query: Optional[str] = None,
        limit: int = 10
    ) -> List[dict]:
        """Retrieve conversation history"""
        if not self.enabled:
            return []
            
        try:
            if query:
                # Semantic search for relevant context (Mem0 v2 API)
                results = await self.client.search(
                    query=query,
                    filters={"user_id": session_id},
                    limit=limit
                )
            else:
                # Get all memories for this session
                results = await self.client.get_all(
                    user_id=session_id
                )
            
            return results if results else []
        except Exception as e:
            logger.warning(f"Mem0 get failed: {e}")
            return []
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def get_semantic_context(
        self,
        session_id: str,
        query: str,
        limit: int = 5
    ) -> str:
        """
        Get semantically relevant context for a query.
        
        Args:
            session_id: Session identifier
            query: Current user query
            limit: Max memories to retrieve
        
        Returns:
            Formatted context string
        """
        if not self.enabled:
            return ""
        
        try:
            # Mem0 v2 API requires filters parameter
            results = await self.client.search(
                query=query,
                filters={"user_id": session_id},
                limit=limit
            )
            
            if not results:
                return ""
            
            # Format results into context
            context_parts = []
            for item in results:
                if isinstance(item, dict):
                    memory = item.get("memory", item.get("content", ""))
                    if memory:
                        context_parts.append(f"- {memory}")
                elif isinstance(item, str):
                    context_parts.append(f"- {item}")
            
            if context_parts:
                return "Relevant conversation context:\n" + "\n".join(context_parts)
            return ""
            
        except Exception as e:
            logger.warning(f"Mem0 semantic search failed: {e}")
            return ""
    
    def extract_facts_from_message(self, user_message: str, assistant_response: str) -> Dict[str, Any]:
        """
        Extract key facts and preferences from a conversation turn.
        
        Args:
            user_message: The user's message
            assistant_response: The assistant's response
        
        Returns:
            Dict with extracted facts and preferences
        """
        facts = {
            "mentioned_topics": [],
            "preferences": {},
            "entities": []
        }
        
        # Extract topics of interest (what the user is asking about)
        topic_patterns = [
            (r"experience (?:with|in) (.+?)(?:\?|$|\.)", "experience"),
            (r"(?:skills?|expertise) (?:in|with) (.+?)(?:\?|$|\.)", "skills"),
            (r"(?:projects?|work) (?:on|with|about) (.+?)(?:\?|$|\.)", "projects"),
            (r"(?:tell me about|what about|how about) (.+?)(?:\?|$|\.)", "interest"),
        ]
        
        lower_msg = user_message.lower()
        for pattern, category in topic_patterns:
            matches = re.findall(pattern, lower_msg)
            for match in matches:
                facts["mentioned_topics"].append({
                    "topic": match.strip(),
                    "category": category
                })
        
        # Extract company mentions
        company_patterns = [
            r"(?:from|at|with|company is) ([A-Z][A-Za-z0-9\s]+(?:Inc|Corp|LLC|Ltd)?)",
            r"(?:hiring for|recruiting for) ([A-Z][A-Za-z0-9\s]+)"
        ]
        
        for pattern in company_patterns:
            matches = re.findall(pattern, user_message)
            for match in matches:
                if len(match) > 2 and match not in ["Inc", "Corp", "LLC", "Ltd"]:
                    facts["entities"].append({
                        "type": "company",
                        "value": match.strip()
                    })
        
        # Extract role/position mentions
        role_patterns = [
            r"(?:hiring|looking) for (?:a )?(.+?(?:engineer|developer|intern|role|position))",
            r"(?:ML|AI|Software|Data|Backend|Frontend) (?:Engineer|Developer|Scientist)"
        ]
        
        for pattern in role_patterns:
            matches = re.findall(pattern, user_message, re.IGNORECASE)
            for match in matches:
                facts["entities"].append({
                    "type": "role",
                    "value": match.strip() if isinstance(match, str) else match
                })
        
        return facts
    
    async def clear_session(self, session_id: str) -> bool:
        """Clear all memories for a session"""
        if not self.enabled:
            return False
            
        try:
            await self.client.delete_all(user_id=session_id)
            return True
        except Exception as e:
            logger.warning(f"Mem0 clear failed: {e}")
            return False
