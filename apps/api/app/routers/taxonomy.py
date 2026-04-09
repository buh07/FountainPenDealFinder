from fastapi import APIRouter

from ..schemas import TaxonomyStandardResponse
from ..services.taxonomy import taxonomy_standard

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("/standard", response_model=TaxonomyStandardResponse)
def get_taxonomy_standard() -> TaxonomyStandardResponse:
    payload = taxonomy_standard()
    return TaxonomyStandardResponse(**payload)
