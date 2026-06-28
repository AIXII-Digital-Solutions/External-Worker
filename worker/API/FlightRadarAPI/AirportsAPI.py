import asyncio
from typing import Iterable, Optional

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Config import setup_logger
from settings import FLIGHT_RADAR_URL, FLIGHT_RADAR_HEADERS
from Database import DatabaseClient
from Database.Models import Airports, AirportRunways
from Utils import str_to_list


logger = setup_logger("flightradar_airports")


async def fetch_airport(
        session: aiohttp.ClientSession,
        code: str,
) -> Optional[dict]:
    async with session.get(f"{FLIGHT_RADAR_URL}/static/airports/{code}/full", headers=FLIGHT_RADAR_HEADERS) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def airport_exists(
        session: AsyncSession,
        iata: Optional[str],
        icao: Optional[str],
) -> bool:
    stmt = select(Airports.id)

    if icao and iata:
        stmt = stmt.where(
            (Airports.icao == icao) | (Airports.iata == iata)
        )
    elif icao:
        stmt = stmt.where(Airports.icao == icao)
    elif iata:
        stmt = stmt.where(Airports.iata == iata)
    else:
        return False

    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def save_airport(session: AsyncSession, data: dict) -> None:
    country = data.get("country")
    timezone = data.get("timezone")
    airport = Airports(
        name=data.get("name"),
        iata=data.get("iata"),
        icao=data.get("icao"),
        lon=data.get("lon"),
        lat=data.get("lat"),
        elevation=data.get("elevation"),
        city=data.get("city"),
        state=data.get("state"),
        country_code=country.get("code"),
        country_name=country.get("name"),
        timezone_name=timezone.get("name"),
        timezone_offset=timezone.get("offset"),
    )

    for rw in data.get("runways", []):
        thr = rw.get("thr_coordinates") or []
        if len(thr) != 2:
            thr_lat = 0.0
            thr_lon = 0.0
        else:
            thr_lat = thr[0]
            thr_lon = thr[1]

        surface = rw.get("surface")
        airport.runways.append(
            AirportRunways(
                designator=rw.get("designator"),
                heading=rw.get("heading"),
                length=rw.get("length"),
                width=rw.get("width"),
                elevation=rw.get("elevation"),
                thr_lat=thr_lat,
                thr_lon=thr_lon,
                surface_type=surface.get("type"),
                surface_description=surface.get("description"),
            )
        )

    session.add(airport)
    logger.debug("Airport saved")


async def load_airports(codes: Iterable[str]) -> None:
    client = DatabaseClient()
    async with aiohttp.ClientSession() as http:
        async with client.session("flightradar") as session:
            logger.info(f"Codes: {', '.join(codes)}")
            for code in codes:
                exists = await airport_exists(
                    session,
                    iata=code,
                    icao=code,
                )
                if exists:
                    logger.debug(f"Airport {code} exists")
                    continue

                data = await fetch_airport(http, code)
                if not data:
                    logger.debug(f"No airport data for {code}")
                    continue

                await save_airport(session, data)
                await asyncio.sleep(2)

            await session.commit()


if __name__ == "__main__":

    # airport_codes = [
    #
    # ]

    airport_codes = str_to_list(
        """
        NJK
NQX
IPL
HII
MEI
NMM
CBM
OSN
YUM
LIH
ULD
NQI
CXL
PPL
DLF
PEL
SUL
FCA
SKQ
LES
SHZ
JRS
NIP
SBA
ALI
KMH
MYL
RDM
SNA
BIY
FLG
VNY
CRP
EED
HHH
LWS
MAF
NEW
NTD
SHR
SLE
AZA
BWO
HVR
KLZ
QSO
THB
APA
CLD
FCB
OMK
ORL
QNN
RVO
STS
ABO
ABY
BCT
BUR
CEU
ESN
EUG
HSH
MNZ
PDG
PIH
SPW
TCL
YEV
AUO
BWC
COF
CRG
DOM
DRT
GSS
HDN
LGB
ONO
PHF
PKU
QTZ
RKV
SUA
SWU
TDW
WGO
ACK
AGS
AOC
ARA
ASE
BLM
CVO
DHA
EGS
EVV
EWN
GCJ
HST
IVG
IYO
JOG
KQT
KTN
LCV
LIT
LMS
MFR
MGR
MMH
MTZ
MWC
MZB
NCO
PGD
QEJ
QQN
QSP
QYA
QYK
QYL
RBK
RDD
RKD
STL
TEA
TGV
TNJ
TVI
TVL
VCT
VPS
WJU
XNA
YBL
YHM
YTR
YXJ
YYD

AGF
AST
AVX
BBX
BPT
BRO
BTP
CDV
CGF
CGI
CHA
CHF
CHL
CHN
CID
CLL
CMH
COD
COI
DIM
DLS
DPA
DTN
DWH
EKI
EKO
ELD
ELH
EME
ENA
EPA
FCH
FRD
FUL
GCY
GKT
GSO
HAR
HIO
HQM
HYA
ITH
JAD
JAF
JQE
JZI
KYE
LAK
LAL
LCI
LFT
LGA
LIX
LKZ
LMO
LNA
LUL
LUW
LVK
LVM
MCI
MCN
MDJ
MFE
MHG
MIV
MRB
MSC
NKU
NZY
OCE
OGD
OMA
OSX
OTG
OWD
PAM
PGA
PIA
PMD
POU
PRC
PUB
PWN
PWY
QGS
QIE
QIP
QKM
QQF
QQL
QSI
QSS
QSY
QTB
QTC
QYS
RAC
RBG
RKH
RST
RYB
RZN
SAF
SBD
SCE
SGU
SHV
SLU
SNS
SOP
TIW
TKF
TRI
TRM
TTN
TUL
TVC
TZN
ULX
URO
USM
UST
VDF
VLD
VRB
VYD
WDB
WGB
WNS
WWD
YQT
YVQ
ZFN
        """
    )


    asyncio.run(load_airports(airport_codes))
