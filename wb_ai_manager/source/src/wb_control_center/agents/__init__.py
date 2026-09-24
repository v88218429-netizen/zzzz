from .api_health import ApiHealthAgent
from .cards import CardsAgent
from .advertising import AdvertisingMonitorAgent, AdvertisingOptimizerAgent
from .search_positions import SearchPositionsAgent
from .inventory import InventoryAgent
from .supply import SupplyAgent
from .funnel import FunnelAgent
from .price_margin import PriceMarginAgent
from .finance import FinanceAgent
from .reviews import ReviewsQuestionsAgent
from .orders import OrdersFBSAgent
from .returns_quality import ReturnsQualityAgent
from .competitors import CompetitorsAgent
from .experiments import ExperimentsAgent
from .supervisor import SupervisorAgent
from .cost_guard import CostGuardAgent
from .buyer_chats import BuyerChatsAgent
from .documents import DocumentsAgent

AGENT_CLASSES = {
    cls.name: cls
    for cls in [
        ApiHealthAgent,
        CardsAgent,
        AdvertisingMonitorAgent,
        AdvertisingOptimizerAgent,
        SearchPositionsAgent,
        InventoryAgent,
        SupplyAgent,
        FunnelAgent,
        PriceMarginAgent,
        FinanceAgent,
        ReviewsQuestionsAgent,
        OrdersFBSAgent,
        ReturnsQualityAgent,
        CompetitorsAgent,
        ExperimentsAgent,
        SupervisorAgent,
        CostGuardAgent,
        BuyerChatsAgent,
        DocumentsAgent,
    ]
}
