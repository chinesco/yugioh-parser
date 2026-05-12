import logging
import requests
from typing import Any

from yugioh_reader.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

class LookupYugiohCardTool(Tool):
    """Look up a Yu-Gi-Oh! card by name."""

    name = "lookup_yugioh_card"
    description = "Look up a Yu-Gi-Oh! card by name to get its text and the sets it belongs to."
    parameters_schema = {
        "type": "object",
        "properties": {
            "card_name": {
                "type": "string",
                "description": "The name of the Yu-Gi-Oh! card to look up.",
            },
        },
        "required": ["card_name"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        card_name = kwargs.get("card_name")
        logger.info(f"LookupYugiohCardTool called for: {card_name}")
        
        try:
            url = f"https://db.ygoprodeck.com/api/v7/cardinfo.php?fname={card_name}"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            if not data.get("data"):
                return {"error": "Card not found"}
            
            card = data["data"][0] # take the first match
            
            name = card.get("name")
            desc = card.get("desc")
            
            sets = []
            if "card_sets" in card:
                for s in card["card_sets"][:5]: # Return up to 5 sets to avoid huge output
                    sets.append(s.get("set_name"))
                    
            return {
                "name": name,
                "text": desc,
                "sets": sets
            }
            
        except Exception as e:
            logger.error(f"Error looking up card: {e}")
            return {"error": str(e)}
