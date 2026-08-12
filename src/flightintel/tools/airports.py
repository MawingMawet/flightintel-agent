"""resolve_airport: static name/city/ICAO lookup ("Suvarnabhumi" -> VTBS).

v1 is a deliberately dumb curated table covering the airports observed in
the data; fuzzy or embedding-based resolution is the phase 2 entry point
for semantic search (architecture.md). An unknown ICAO is reported as
unresolved rather than guessed.
"""

from pydantic import BaseModel


class Airport(BaseModel):
    icao: str
    name: str
    city: str


class ResolveAirportInput(BaseModel):
    query: str


class ResolveAirportResult(BaseModel):
    query: str
    matches: list[Airport]
    resolved: bool
    note: str | None = None


# icao -> (name, city, extra aliases)
_AIRPORTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "VTBS": ("Suvarnabhumi Airport", "Bangkok", ("bkk",)),
    "VTBD": ("Don Mueang International Airport", "Bangkok", ("don muang", "dmk")),
    "VTBU": ("U-Tapao International Airport", "Rayong / Pattaya", ("utapao",)),
    "VTCC": ("Chiang Mai International Airport", "Chiang Mai", ()),
    "VTCT": ("Mae Fah Luang Chiang Rai International Airport", "Chiang Rai", ()),
    "VVNB": ("Noi Bai International Airport", "Hanoi", ()),
    "VHHH": ("Hong Kong International Airport", "Hong Kong", ("chek lap kok",)),
    "VMMC": ("Macau International Airport", "Macau", ()),
    "ZGSZ": ("Shenzhen Bao'an International Airport", "Shenzhen", ()),
    "WSSS": ("Singapore Changi Airport", "Singapore", ("changi",)),
    "WSSL": ("Seletar Airport", "Singapore", ()),
    "WIII": ("Soekarno-Hatta International Airport", "Jakarta", ()),
    "WMKK": ("Kuala Lumpur International Airport", "Kuala Lumpur", ("klia",)),
    "WMKP": ("Penang International Airport", "Penang", ()),
    "WMKJ": ("Senai International Airport", "Johor Bahru", ()),
    "WMSA": ("Sultan Abdul Aziz Shah Airport", "Subang / Kuala Lumpur", ("subang",)),
    "WBSB": ("Brunei International Airport", "Bandar Seri Begawan", ()),
    "RJTT": ("Tokyo Haneda Airport", "Tokyo", ("haneda",)),
    "RJAA": ("Narita International Airport", "Tokyo", ("narita",)),
    "RJBB": ("Kansai International Airport", "Osaka", ("kansai",)),
    "RJCC": ("New Chitose Airport", "Sapporo", ()),
    "RJFF": ("Fukuoka Airport", "Fukuoka", ()),
    "RKSI": ("Incheon International Airport", "Seoul", ("incheon",)),
    "RKSS": ("Gimpo International Airport", "Seoul", ("gimpo",)),
    "RKPK": ("Gimhae International Airport", "Busan", ("gimhae",)),
    "RCTP": ("Taiwan Taoyuan International Airport", "Taipei", ("taoyuan",)),
    "RCSS": ("Taipei Songshan Airport", "Taipei", ("songshan",)),
    "RCKH": ("Kaohsiung International Airport", "Kaohsiung", ()),
    "RPLL": ("Ninoy Aquino International Airport", "Manila", ()),
    "VIDP": ("Indira Gandhi International Airport", "Delhi", ()),
    "VABB": ("Chhatrapati Shivaji Maharaj International Airport", "Mumbai", ()),
    "VECC": ("Netaji Subhas Chandra Bose International Airport", "Kolkata", ()),
    "VOBL": ("Kempegowda International Airport", "Bengaluru", ("bangalore",)),
    "VOCI": ("Cochin International Airport", "Kochi", ("cochin",)),
    "VGZR": ("Hazrat Shahjalal International Airport", "Dhaka", ()),
    "VNKT": ("Tribhuvan International Airport", "Kathmandu", ()),
    "VYYY": ("Yangon International Airport", "Yangon", ()),
    "VYMD": ("Mandalay International Airport", "Mandalay", ()),
    "OMDB": ("Dubai International Airport", "Dubai", ()),
    "OMSJ": ("Sharjah International Airport", "Sharjah", ()),
    "OTHH": ("Hamad International Airport", "Doha", ("hamad",)),
    "OBBI": ("Bahrain International Airport", "Bahrain", ()),
    "LTFM": ("Istanbul Airport", "Istanbul", ()),
    "LLBG": ("Ben Gurion Airport", "Tel Aviv", ("ben gurion",)),
    "YSSY": ("Sydney Kingsford Smith Airport", "Sydney", ()),
    "YMML": ("Melbourne Airport", "Melbourne", ()),
    "YBBN": ("Brisbane Airport", "Brisbane", ()),
    "YPPH": ("Perth Airport", "Perth", ()),
    "PANC": ("Ted Stevens Anchorage International Airport", "Anchorage", ()),
    "CYVR": ("Vancouver International Airport", "Vancouver", ()),
    "EGLL": ("London Heathrow Airport", "London", ("heathrow",)),
    "EGKK": ("London Gatwick Airport", "London", ("gatwick",)),
    "LFPG": ("Paris Charles de Gaulle Airport", "Paris", ("cdg", "charles de gaulle")),
    "EHAM": ("Amsterdam Airport Schiphol", "Amsterdam", ("schiphol",)),
    "EDDF": ("Frankfurt Airport", "Frankfurt", ()),
    "EDDM": ("Munich Airport", "Munich", ()),
    "EDDP": ("Leipzig/Halle Airport", "Leipzig", ()),
    "EFHK": ("Helsinki-Vantaa Airport", "Helsinki", ("vantaa",)),
    "ENGM": ("Oslo Gardermoen Airport", "Oslo", ("gardermoen",)),
    "ESSA": ("Stockholm Arlanda Airport", "Stockholm", ("arlanda",)),
    "LSZH": ("Zurich Airport", "Zurich", ()),
    "LOWW": ("Vienna International Airport", "Vienna", ()),
    "LIMC": ("Milan Malpensa Airport", "Milan", ("malpensa",)),
    "LIRF": ("Rome Fiumicino Airport", "Rome", ("fiumicino",)),
    "ELLX": ("Luxembourg Airport", "Luxembourg", ()),
    "FACT": ("Cape Town International Airport", "Cape Town", ()),
    "NWWW": ("La Tontouta International Airport", "Noumea", ()),
}


def resolve_airport(params: ResolveAirportInput) -> ResolveAirportResult:
    q = params.query.strip().casefold()
    if not q:
        return ResolveAirportResult(
            query=params.query, matches=[], resolved=False, note="Empty query."
        )

    if q.upper() in _AIRPORTS:
        name, city, _ = _AIRPORTS[q.upper()]
        return ResolveAirportResult(
            query=params.query,
            matches=[Airport(icao=q.upper(), name=name, city=city)],
            resolved=True,
        )

    matches = [
        Airport(icao=icao, name=name, city=city)
        for icao, (name, city, aliases) in _AIRPORTS.items()
        if q in name.casefold()
        or q in city.casefold()
        or any(q in alias for alias in aliases)
    ]
    note = None
    if not matches:
        note = (
            "No match in the static lookup. If the query is already an ICAO "
            "code, use it directly in SQL; the data may contain airports "
            "this table does not name."
        )
    return ResolveAirportResult(
        query=params.query,
        matches=matches,
        resolved=len(matches) == 1,
        note=note,
    )
