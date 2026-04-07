export interface SearchQuery {
  keyword: string;
  category?: string;
  minPriceJpy?: number;
  maxPriceJpy?: number;
}

export interface RawListing {
  source: string;
  sourceListingId: string;
  title: string;
  url: string;
}

export interface ListingSourceAdapter {
  search(query: SearchQuery): Promise<RawListing[]>;
  fetchListingDetail(sourceId: string): Promise<Record<string, unknown>>;
  fetchListingImages(sourceId: string): Promise<string[]>;
  getFreshWindowListings(windowIso: string, category: string): Promise<RawListing[]>;
}
