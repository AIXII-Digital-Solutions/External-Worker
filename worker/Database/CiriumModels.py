from datetime import date

from sqlalchemy import String, Float, Integer, Boolean, Date, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import CiriumBase as Base


class AircraftRevision(Base):
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True, unique=True)
    data_rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    aircrafts: Mapped[list["CiriumAircrafts"]] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan"
    )

class CiriumAircrafts(Base):
    __table_args__ = (
        Index(
            "ix_cirium_revision_serial",
            "revision_id",
            "Serial Number"
        ),
    )
    revision_id: Mapped[int] = mapped_column(
        ForeignKey(AircraftRevision.id, ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    revision: Mapped["AircraftRevision"] = relationship(back_populates="aircrafts")

    plan_type: Mapped[str] = mapped_column(String, nullable=True, default=None, name="plan_type")

    Type: Mapped[str] = mapped_column(String, nullable=True, name="Type")
    Serial_Number: Mapped[str] = mapped_column(String, nullable=True, name="Serial Number", index=True)
    Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Manufacturer")
    Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Master Series")
    Registration: Mapped[str] = mapped_column(String, nullable=True, name="Registration", index=True)
    Status: Mapped[str] = mapped_column(String, nullable=True, name="Status")
    Age: Mapped[float] = mapped_column(Float, nullable=True, name="Age")

    # Operator / Owner
    Operator: Mapped[str] = mapped_column(String, nullable=True, name="Operator")
    Manager: Mapped[str] = mapped_column(String, nullable=True, name="Manager")
    Owner: Mapped[str] = mapped_column(String, nullable=True, name="Owner")

    # Engines
    Engine_Type: Mapped[str] = mapped_column(String, nullable=True, name="Engine Type")
    Engine_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Series")

    # Status dates
    Status_Change_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Status Change Date")
    Status_Duration_years: Mapped[float] = mapped_column(Float, nullable=True, name="Status Duration (years)")

    # Insurance
    Hull_Insurance_Placement_Group: Mapped[str] = mapped_column(String, nullable=True, name="Hull Insurance Placement Group")

    # Weights (lbs)
    Certified_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Certified MTOW (lbs)")
    Operating_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating MTOW (lbs)")
    Max_Landing_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Landing Weight (lbs)")
    Max_Zero_Fuel_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Zero Fuel Weight (lbs)")
    Operating_Empty_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating Empty Weight (lbs)")
    Max_Payload_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Payload (lbs)")
    Max_Cargo_Volume_cuft: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Cargo Volume (cubic feet)")

    Fuel_Capacity_USG: Mapped[float] = mapped_column(Float, nullable=True, name="Fuel Capacity (US gallons)")
    Noise_Category: Mapped[str] = mapped_column(String, nullable=True, name="Noise Category")

    Age_at_Retirement_or_Written_Off: Mapped[float] = mapped_column(Float, nullable=True, name="Age at Retirement/Written Off")

    # IDs
    FG_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="FG ID")
    Order_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="Order ID")
    Line_Number: Mapped[str] = mapped_column(String, nullable=True, name="Line Number")
    Block_Number: Mapped[str] = mapped_column(String, nullable=True, name="Block Number")
    Fleet_Number: Mapped[str] = mapped_column(String, nullable=True, name="Fleet Number")

    # Country / Geo
    Country_Subregion_of_Registration: Mapped[str] = mapped_column(String, nullable=True, name="Country/Subregion of Registration")

    # Dates
    First_Flight_Date: Mapped[date] = mapped_column(Date, nullable=True, name="First Flight Date")
    Build_Year: Mapped[int] = mapped_column(Integer, nullable=True, name="Build Year")
    Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Delivery Date")
    In_Service_Date: Mapped[date] = mapped_column(Date, nullable=True, name="In Service Date")
    Order_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Order Date")

    Primary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Primary Usage")
    Secondary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Secondary Usage")

    Indicative_Market_Value_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Value (US$m)")
    Indicative_Market_Lease_Rate_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Lease Rate (US$m)")

    # Aircraft family info
    Current_Family: Mapped[str] = mapped_column(String, nullable=True, name="Current Family")
    Series: Mapped[str] = mapped_column(String, nullable=True, name="Series")
    Aircraft_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Sub Series")
    Aircraft_Minor_Variant: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Minor Variant")
    Modifiers: Mapped[str] = mapped_column(String, nullable=True, name="Modifiers")

    Number_Of_Engines: Mapped[int] = mapped_column(Integer, nullable=True, name="Number Of Engines")
    Engine_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Engine Manufacturer")
    Engine_Family: Mapped[str] = mapped_column(String, nullable=True, name="Engine Family")
    Engine_Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Master Series")
    Engine_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Sub Series")
    enginepropulsiontypename: Mapped[str] = mapped_column(String, nullable=True, name="enginepropulsiontypename")

    Market_Sector: Mapped[str] = mapped_column(String, nullable=True, name="Market Sector")
    Market_Class: Mapped[str] = mapped_column(String, nullable=True, name="Market Class")
    Market_Grouping: Mapped[str] = mapped_column(String, nullable=True, name="Market Grouping")

    Soviet_Built: Mapped[bool] = mapped_column(Boolean, nullable=True, name="Soviet Built")

    # Leasing
    Lease_Type: Mapped[str] = mapped_column(String, nullable=True, name="Lease Type")
    Lease_Dry_Wet: Mapped[str] = mapped_column(String, nullable=True, name="Lease Dry / Wet")
    Lease_Start: Mapped[date] = mapped_column(Date, nullable=True, name="Lease Start")
    Lease_End: Mapped[date] = mapped_column(Date, nullable=True, name="Lease End")
    Lease_Duration_months: Mapped[float] = mapped_column(Float, nullable=True, name="Lease Duration (months)")
    Is_Lease_End_Estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Is Lease End Estimated")

    # Base airport
    Base_Airport_Region: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Region")
    Base_Airport_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Country/Subregion")
    Base_Airport_State: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport State")
    Base_Airport_City: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport City")
    Base_Airport_Name: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Name")
    Base_Airport_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport ICAO")
    Base_Airport_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport IATA")

    # Build location
    Build_Region: Mapped[str] = mapped_column(String, nullable=True, name="Build Region")
    Build_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Build Country/Subregion")
    Build_State: Mapped[str] = mapped_column(String, nullable=True, name="Build State")
    Build_City: Mapped[str] = mapped_column(String, nullable=True, name="Build City")
    Build_Location: Mapped[str] = mapped_column(String, nullable=True, name="Build Location")
    Build_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Build ICAO")
    Build_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Build IATA")

    # Trust owner
    Trust_Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Region")
    Trust_Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Country/Subregion")
    Trust_Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner State")
    Trust_Owner: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner")
    Trust_Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Category")
    Trust_Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Type")
    Trust_Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Status")

    # "Operated For"
    Operated_For_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Region")
    Operated_For_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Country/Subregion")
    Operated_For_State: Mapped[str] = mapped_column(String, nullable=True, name="Operated For State")
    Operated_For: Mapped[str] = mapped_column(String, nullable=True, name="Operated For")
    Operated_For_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Category")
    Operated_For_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Type")
    Operated_For_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Status")

    # Operator group
    Operator_Group_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Region")
    Operator_Group_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Country/Subregion")
    Operator_Group_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group State")
    Operator_Group: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group")
    Operator_Group_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Category")
    Operator_Group_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Type")
    Operator_Group_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Status")

    # Operational lessor
    Operational_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor")
    Operational_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Region")
    Operational_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Country/Subregion")
    Operational_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor State")
    Operational_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Category")
    Operational_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Type")
    Operational_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Status")

    # Sub-lessor
    Sub_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Region")
    Sub_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Country/Subregion")
    Sub_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor State")
    Sub_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor")
    Sub_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Category")
    Sub_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Type")
    Sub_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Status")

    # Manager
    Manager_Region: Mapped[str] = mapped_column(String, nullable=True, name="Manager Region")
    Manager_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Manager Country/Subregion")
    Manager_State: Mapped[str] = mapped_column(String, nullable=True, name="Manager State")
    Manager_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Category")
    Manager_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Type")
    Manager_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Status")

    # Operator
    Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Region")
    Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Country/Subregion")
    Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator State")
    Operator_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Operator IATA")
    Operator_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Operator ICAO")
    Operator_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Category")
    Operator_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Type")
    Operator_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Status")
    Operator_Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Operator Delivery Date")
    Duration_With_Operator_months: Mapped[float] = mapped_column(Float, nullable=True, name="Duration With Operator (months)")

    # Original operator
    Original_Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Region")
    Original_Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Country/Subregion")
    Original_Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator State")
    Original_Operator: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator")
    Original_Operator_Category: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Category")
    Original_Operator_Type: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Type")
    Original_Operator_Status: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Status")

    # Owner
    Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Owner Region")
    Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Owner Country/Subregion")
    Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Owner State")
    Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Category")
    Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Type")
    Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Status")

    Participants: Mapped[str] = mapped_column(String, nullable=True, name="Participants")

    # APU
    APU_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="APU Manufacturer")
    APU_Type: Mapped[str] = mapped_column(String, nullable=True, name="APU Type")
    APU_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="APU Sub Series")

    # Seats & cabin
    Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats")

    # Business, First, Premium, Economy, VIP, Utility
    Economy_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Cabin Name")
    Economy_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet Model")
    Economy_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet OEM")
    Economy_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Converted Seats")
    Economy_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Convertible Seats")
    Economy_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Seats")
    Economy_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Paid Connectivity")
    Economy_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone Model")
    Economy_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone OEM")
    Economy_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Power Outlet")
    Economy_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE Model")
    Economy_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE OEM")
    Economy_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Primary IFE Screen Size (in)")
    Economy_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Model")
    Economy_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat OEM")
    Economy_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Pitch (in)")
    Economy_Class_Seat_Recline_deg: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (deg)")
    Economy_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (in)")
    Economy_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Abreast")
    Economy_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Converted To Class")
    Economy_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Support OEM")
    Economy_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Width (in)")

    Business_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Cabin Name")
    Business_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet Model")
    Business_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet OEM")
    Business_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Converted Seats")
    Business_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Convertible Seats")
    Business_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats")
    Business_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Paid Connectivity")
    Business_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone Model")
    Business_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone OEM")
    Business_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Power Outlet")
    Business_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE Model")
    Business_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE OEM")
    Business_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Primary IFE Screen Size (in)")
    Business_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Model")
    Business_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat OEM")
    Business_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Pitch (in)")
    Business_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Seat Recline (deg)")
    Business_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Recline (in)")
    Business_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Abreast")
    Business_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Converted To Class")
    Business_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Support OEM")
    Business_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Width (in)")

    Other_Utility_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Cabin Name")
    Other_Utility_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet Model")
    Other_Utility_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet OEM")
    Other_Utility_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Converted Seats")
    Other_Utility_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Convertible Seats")
    Other_Utility_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats")
    Other_Utility_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Paid Connectivity")
    Other_Utility_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone Model")
    Other_Utility_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone OEM")
    Other_Utility_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Power Outlet")
    Other_Utility_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE Model")
    Other_Utility_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE OEM")
    Other_Utility_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Primary IFE Screen Size (in)")
    Other_Utility_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Model")
    Other_Utility_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat OEM")
    Other_Utility_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Pitch (in)")
    Other_Utility_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Recline (deg)")
    Other_Utility_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Recline (in)")
    Other_Utility_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seats Abreast")
    Other_Utility_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Other Utility Seats Converted To Class")
    Other_Utility_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Support OEM")
    Other_Utility_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Width (in)")

    First_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="First Class Cabin Name")
    First_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet Model")
    First_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet OEM")
    First_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Converted Seats")
    First_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Convertible Seats")
    First_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats")
    First_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="First Class Paid Connectivity")
    First_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone Model")
    First_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone OEM")
    First_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="First Class Power Outlet")
    First_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE Model")
    First_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE OEM")
    First_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Primary IFE Screen Size (in)")
    First_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Model")
    First_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat OEM")
    First_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Pitch (in)")
    First_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Seat Recline (deg)")
    First_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Recline (in)")
    First_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Abreast")
    First_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Converted To Class")
    First_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Support OEM")
    First_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Width (in)")

    Premium_Economy_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Cabin Name")
    Premium_Economy_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet Model")
    Premium_Economy_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet OEM")
    Premium_Economy_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Converted Seats")
    Premium_Economy_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Convertible Seats")
    Premium_Economy_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats")
    Premium_Economy_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Paid Connectivity")
    Premium_Economy_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone Model")
    Premium_Economy_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone OEM")
    Premium_Economy_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Power Outlet")
    Premium_Economy_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE Model")
    Premium_Economy_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE OEM")
    Premium_Economy_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Primary IFE Screen Size (in)")
    Premium_Economy_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Model")
    Premium_Economy_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat OEM")
    Premium_Economy_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Pitch (in)")
    Premium_Economy_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Seat Recline (deg)")
    Premium_Economy_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Recline (in)")
    Premium_Economy_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Abreast")
    Premium_Economy_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Converted To Class")
    Premium_Economy_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Support OEM")
    Premium_Economy_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Width (in)")

    VIP_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="VIP Cabin Name")
    VIP_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet Model")
    VIP_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet OEM")
    VIP_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Converted Seats")
    VIP_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Convertible Seats")
    VIP_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats")
    VIP_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="VIP Paid Connectivity")
    VIP_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone Model")
    VIP_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone OEM")
    VIP_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="VIP Power Outlet")
    VIP_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE Model")
    VIP_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE OEM")
    VIP_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Primary IFE Screen Size (in)")
    VIP_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Model")
    VIP_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat OEM")
    VIP_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Pitch (in)")
    VIP_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (deg)")
    VIP_Seat_Recline_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (in)")
    VIP_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Abreast")
    VIP_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Converted To Class")
    VIP_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Support OEM")
    VIP_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Seat Width (in)")

    # Flight hours & cycles
    Cumulative_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours")
    Cumulative_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles")
    Reported_Hours_and_Cycles_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Reported Hours and Cycles Date")

    Average_Flight_Time: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time")
    Average_Annual_Cycles: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Cycles")
    Average_Annual_Hours: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Hours")

    Previous_Month_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Cycles")
    Previous_Month_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Hours")

    Previous_12_Months_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Cycles")
    Previous_12_Months_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Hours")

    Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Average Daily Utilisation")
    Previous_12_Months_Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Previous 12 Months Average Daily Utilisation")

    Cumulative_Hours_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours With Operator")
    Cumulative_Cycles_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles With Operator")
    Average_Flight_Time_With_Operator: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time With Operator")

    # Storage / conversion location
    Storage_Conversion_Location_Region_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Region Name")
    Storage_Conversion_Location_Country_Subregion_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Country/Subregion Name")
    Storage_Conversion_Location_State_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location State Name")
    Storage_Conversion_Location_City_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location City Name")
    Storage_Conversion_Location_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Name")

    Aircraft_Class: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Class")

    Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats estimated")

    # Multi-configuration flags
    Business_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Multiple Configurations exist")
    Business_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats estimated")

    Economy_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Multiple Configurations exist")
    Economy_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer,  nullable=True, name="Economy Class Number of Seats estimated")

    First_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Multiple Configurations exist")
    First_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats estimated")

    Other_Utility_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Multiple Configurations exist")
    Other_Utility_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats estimated")

    Premium_Economy_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Multiple Configurations exist")
    Premium_Economy_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats estimated")

    VIP_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Multiple Configurations exist")
    VIP_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats estimated")


class ASGAircrafts(Base):
    plan_type: Mapped[str] = mapped_column(String, nullable=True, default=None, name="plan_type")

    Airline: Mapped[str] = mapped_column(String, nullable=True, name="Airline")
    Type: Mapped[str] = mapped_column(String, nullable=True, name="Type")
    Serial_Number: Mapped[str] = mapped_column(String, nullable=True, name="Serial Number")
    Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Manufacturer")
    Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Master Series")
    Registration: Mapped[str] = mapped_column(String, nullable=True, name="Registration")
    Status: Mapped[str] = mapped_column(String, nullable=True, name="Status")
    Age: Mapped[float] = mapped_column(Float, nullable=True, name="Age")

    # Operator / Owner
    Operator: Mapped[str] = mapped_column(String, nullable=True, name="Operator")
    Manager: Mapped[str] = mapped_column(String, nullable=True, name="Manager")
    Owner: Mapped[str] = mapped_column(String, nullable=True, name="Owner")

    # Engines
    Engine_Type: Mapped[str] = mapped_column(String, nullable=True, name="Engine Type")
    Engine_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Series")

    # Status dates
    Status_Change_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Status Change Date")
    Status_Duration_years: Mapped[float] = mapped_column(Float, nullable=True, name="Status Duration (years)")

    # Insurance
    Hull_Insurance_Placement_Group: Mapped[str] = mapped_column(String, nullable=True, name="Hull Insurance Placement Group")

    # Weights (lbs)
    Certified_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Certified MTOW (lbs)")
    Operating_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating MTOW (lbs)")
    Max_Landing_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Landing Weight (lbs)")
    Max_Zero_Fuel_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Zero Fuel Weight (lbs)")
    Operating_Empty_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating Empty Weight (lbs)")
    Max_Payload_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Payload (lbs)")
    Max_Cargo_Volume_cuft: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Cargo Volume (cubic feet)")

    Fuel_Capacity_USG: Mapped[float] = mapped_column(Float, nullable=True, name="Fuel Capacity (US gallons)")
    Noise_Category: Mapped[str] = mapped_column(String, nullable=True, name="Noise Category")

    Age_at_Retirement_or_Written_Off: Mapped[float] = mapped_column(Float, nullable=True, name="Age at Retirement/Written Off")

    # IDs
    FG_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="FG ID")
    Order_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="Order ID")
    Line_Number: Mapped[str] = mapped_column(String, nullable=True, name="Line Number")
    Block_Number: Mapped[str] = mapped_column(String, nullable=True, name="Block Number")
    Fleet_Number: Mapped[str] = mapped_column(String, nullable=True, name="Fleet Number")

    # Country / Geo
    Country_Subregion_of_Registration: Mapped[str] = mapped_column(String, nullable=True, name="Country/Subregion of Registration")

    # Dates
    First_Flight_Date: Mapped[date] = mapped_column(Date, nullable=True, name="First Flight Date")
    Build_Year: Mapped[int] = mapped_column(Integer, nullable=True, name="Build Year")
    Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Delivery Date")
    In_Service_Date: Mapped[date] = mapped_column(Date, nullable=True, name="In Service Date")
    Order_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Order Date")

    Primary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Primary Usage")
    Secondary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Secondary Usage")

    Indicative_Market_Value_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Value (US$m)")
    Indicative_Market_Lease_Rate_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Lease Rate (US$m)")

    # Aircraft family info
    Current_Family: Mapped[str] = mapped_column(String, nullable=True, name="Current Family")
    Series: Mapped[str] = mapped_column(String, nullable=True, name="Series")
    Aircraft_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Sub Series")
    Aircraft_Minor_Variant: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Minor Variant")
    Modifiers: Mapped[str] = mapped_column(String, nullable=True, name="Modifiers")

    Number_Of_Engines: Mapped[int] = mapped_column(Integer, nullable=True, name="Number Of Engines")
    Engine_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Engine Manufacturer")
    Engine_Family: Mapped[str] = mapped_column(String, nullable=True, name="Engine Family")
    Engine_Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Master Series")
    Engine_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Sub Series")
    enginepropulsiontypename: Mapped[str] = mapped_column(String, nullable=True, name="enginepropulsiontypename")

    Market_Sector: Mapped[str] = mapped_column(String, nullable=True, name="Market Sector")
    Market_Class: Mapped[str] = mapped_column(String, nullable=True, name="Market Class")
    Market_Grouping: Mapped[str] = mapped_column(String, nullable=True, name="Market Grouping")

    Soviet_Built: Mapped[bool] = mapped_column(Boolean, nullable=True, name="Soviet Built")

    # Leasing
    Lease_Type: Mapped[str] = mapped_column(String, nullable=True, name="Lease Type")
    Lease_Dry_Wet: Mapped[str] = mapped_column(String, nullable=True, name="Lease Dry / Wet")
    Lease_Start: Mapped[date] = mapped_column(Date, nullable=True, name="Lease Start")
    Lease_End: Mapped[date] = mapped_column(Date, nullable=True, name="Lease End")
    Lease_Duration_months: Mapped[float] = mapped_column(Float, nullable=True, name="Lease Duration (months)")
    Is_Lease_End_Estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Is Lease End Estimated")

    # Base airport
    Base_Airport_Region: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Region")
    Base_Airport_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Country/Subregion")
    Base_Airport_State: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport State")
    Base_Airport_City: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport City")
    Base_Airport_Name: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Name")
    Base_Airport_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport ICAO")
    Base_Airport_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport IATA")

    # Build location
    Build_Region: Mapped[str] = mapped_column(String, nullable=True, name="Build Region")
    Build_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Build Country/Subregion")
    Build_State: Mapped[str] = mapped_column(String, nullable=True, name="Build State")
    Build_City: Mapped[str] = mapped_column(String, nullable=True, name="Build City")
    Build_Location: Mapped[str] = mapped_column(String, nullable=True, name="Build Location")
    Build_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Build ICAO")
    Build_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Build IATA")

    # Trust owner
    Trust_Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Region")
    Trust_Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Country/Subregion")
    Trust_Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner State")
    Trust_Owner: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner")
    Trust_Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Category")
    Trust_Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Type")
    Trust_Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Status")

    # "Operated For"
    Operated_For_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Region")
    Operated_For_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Country/Subregion")
    Operated_For_State: Mapped[str] = mapped_column(String, nullable=True, name="Operated For State")
    Operated_For: Mapped[str] = mapped_column(String, nullable=True, name="Operated For")
    Operated_For_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Category")
    Operated_For_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Type")
    Operated_For_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Status")

    # Operator group
    Operator_Group_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Region")
    Operator_Group_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Country/Subregion")
    Operator_Group_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group State")
    Operator_Group: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group")
    Operator_Group_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Category")
    Operator_Group_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Type")
    Operator_Group_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Status")

    # Operational lessor
    Operational_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor")
    Operational_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Region")
    Operational_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Country/Subregion")
    Operational_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor State")
    Operational_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Category")
    Operational_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Type")
    Operational_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Status")

    # Sub-lessor
    Sub_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Region")
    Sub_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Country/Subregion")
    Sub_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor State")
    Sub_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor")
    Sub_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Category")
    Sub_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Type")
    Sub_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Status")

    # Manager
    Manager_Region: Mapped[str] = mapped_column(String, nullable=True, name="Manager Region")
    Manager_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Manager Country/Subregion")
    Manager_State: Mapped[str] = mapped_column(String, nullable=True, name="Manager State")
    Manager_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Category")
    Manager_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Type")
    Manager_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Status")

    # Operator
    Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Region")
    Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Country/Subregion")
    Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator State")
    Operator_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Operator IATA")
    Operator_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Operator ICAO")
    Operator_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Category")
    Operator_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Type")
    Operator_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Status")
    Operator_Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Operator Delivery Date")
    Duration_With_Operator_months: Mapped[float] = mapped_column(Float, nullable=True, name="Duration With Operator (months)")

    # Original operator
    Original_Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Region")
    Original_Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Country/Subregion")
    Original_Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator State")
    Original_Operator: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator")
    Original_Operator_Category: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Category")
    Original_Operator_Type: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Type")
    Original_Operator_Status: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Status")

    # Owner
    Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Owner Region")
    Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Owner Country/Subregion")
    Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Owner State")
    Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Category")
    Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Type")
    Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Status")

    Participants: Mapped[str] = mapped_column(String, nullable=True, name="Participants")

    # APU
    APU_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="APU Manufacturer")
    APU_Type: Mapped[str] = mapped_column(String, nullable=True, name="APU Type")
    APU_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="APU Sub Series")

    # Seats & cabin
    Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats")

    # Business, First, Premium, Economy, VIP, Utility
    Economy_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Cabin Name")
    Economy_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet Model")
    Economy_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet OEM")
    Economy_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Converted Seats")
    Economy_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Convertible Seats")
    Economy_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Seats")
    Economy_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Paid Connectivity")
    Economy_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone Model")
    Economy_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone OEM")
    Economy_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Power Outlet")
    Economy_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE Model")
    Economy_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE OEM")
    Economy_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Primary IFE Screen Size (in)")
    Economy_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Model")
    Economy_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat OEM")
    Economy_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Pitch (in)")
    Economy_Class_Seat_Recline_deg: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (deg)")
    Economy_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (in)")
    Economy_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Abreast")
    Economy_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Converted To Class")
    Economy_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Support OEM")
    Economy_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Width (in)")

    Business_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Cabin Name")
    Business_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet Model")
    Business_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet OEM")
    Business_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Converted Seats")
    Business_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Convertible Seats")
    Business_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats")
    Business_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Paid Connectivity")
    Business_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone Model")
    Business_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone OEM")
    Business_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Power Outlet")
    Business_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE Model")
    Business_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE OEM")
    Business_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Primary IFE Screen Size (in)")
    Business_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Model")
    Business_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat OEM")
    Business_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Pitch (in)")
    Business_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Seat Recline (deg)")
    Business_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Recline (in)")
    Business_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Abreast")
    Business_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Converted To Class")
    Business_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Support OEM")
    Business_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Width (in)")

    Other_Utility_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Cabin Name")
    Other_Utility_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet Model")
    Other_Utility_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet OEM")
    Other_Utility_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Converted Seats")
    Other_Utility_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Convertible Seats")
    Other_Utility_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats")
    Other_Utility_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Paid Connectivity")
    Other_Utility_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone Model")
    Other_Utility_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone OEM")
    Other_Utility_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Power Outlet")
    Other_Utility_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE Model")
    Other_Utility_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE OEM")
    Other_Utility_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Primary IFE Screen Size (in)")
    Other_Utility_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Model")
    Other_Utility_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat OEM")
    Other_Utility_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Pitch (in)")
    Other_Utility_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Recline (deg)")
    Other_Utility_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Recline (in)")
    Other_Utility_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seats Abreast")
    Other_Utility_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Other Utility Seats Converted To Class")
    Other_Utility_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Support OEM")
    Other_Utility_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Width (in)")

    First_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="First Class Cabin Name")
    First_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet Model")
    First_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet OEM")
    First_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Converted Seats")
    First_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Convertible Seats")
    First_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats")
    First_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="First Class Paid Connectivity")
    First_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone Model")
    First_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone OEM")
    First_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="First Class Power Outlet")
    First_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE Model")
    First_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE OEM")
    First_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Primary IFE Screen Size (in)")
    First_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Model")
    First_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat OEM")
    First_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Pitch (in)")
    First_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Seat Recline (deg)")
    First_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Recline (in)")
    First_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Abreast")
    First_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Converted To Class")
    First_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Support OEM")
    First_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Width (in)")

    Premium_Economy_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Cabin Name")
    Premium_Economy_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet Model")
    Premium_Economy_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet OEM")
    Premium_Economy_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Converted Seats")
    Premium_Economy_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Convertible Seats")
    Premium_Economy_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats")
    Premium_Economy_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Paid Connectivity")
    Premium_Economy_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone Model")
    Premium_Economy_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone OEM")
    Premium_Economy_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Power Outlet")
    Premium_Economy_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE Model")
    Premium_Economy_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE OEM")
    Premium_Economy_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Primary IFE Screen Size (in)")
    Premium_Economy_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Model")
    Premium_Economy_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat OEM")
    Premium_Economy_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Pitch (in)")
    Premium_Economy_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Seat Recline (deg)")
    Premium_Economy_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Recline (in)")
    Premium_Economy_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Abreast")
    Premium_Economy_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Converted To Class")
    Premium_Economy_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Support OEM")
    Premium_Economy_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Width (in)")

    VIP_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="VIP Cabin Name")
    VIP_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet Model")
    VIP_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet OEM")
    VIP_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Converted Seats")
    VIP_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Convertible Seats")
    VIP_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats")
    VIP_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="VIP Paid Connectivity")
    VIP_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone Model")
    VIP_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone OEM")
    VIP_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="VIP Power Outlet")
    VIP_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE Model")
    VIP_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE OEM")
    VIP_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Primary IFE Screen Size (in)")
    VIP_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Model")
    VIP_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat OEM")
    VIP_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Pitch (in)")
    VIP_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (deg)")
    VIP_Seat_Recline_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (in)")
    VIP_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Abreast")
    VIP_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Converted To Class")
    VIP_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Support OEM")
    VIP_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Seat Width (in)")

    # Flight hours & cycles
    Cumulative_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours")
    Cumulative_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles")
    Reported_Hours_and_Cycles_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Reported Hours and Cycles Date")

    Average_Flight_Time: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time")
    Average_Annual_Cycles: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Cycles")
    Average_Annual_Hours: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Hours")

    Previous_Month_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Cycles")
    Previous_Month_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Hours")

    Previous_12_Months_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Cycles")
    Previous_12_Months_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Hours")

    Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Average Daily Utilisation")
    Previous_12_Months_Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Previous 12 Months Average Daily Utilisation")

    Cumulative_Hours_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours With Operator")
    Cumulative_Cycles_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles With Operator")
    Average_Flight_Time_With_Operator: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time With Operator")

    # Storage / conversion location
    Storage_Conversion_Location_Region_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Region Name")
    Storage_Conversion_Location_Country_Subregion_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Country/Subregion Name")
    Storage_Conversion_Location_State_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location State Name")
    Storage_Conversion_Location_City_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location City Name")
    Storage_Conversion_Location_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Name")

    Aircraft_Class: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Class")

    Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats estimated")

    # Multi-configuration flags
    Business_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Multiple Configurations exist")
    Business_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats estimated")

    Economy_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Multiple Configurations exist")
    Economy_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer,  nullable=True, name="Economy Class Number of Seats estimated")

    First_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Multiple Configurations exist")
    First_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats estimated")

    Other_Utility_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Multiple Configurations exist")
    Other_Utility_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats estimated")

    Premium_Economy_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Multiple Configurations exist")
    Premium_Economy_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats estimated")

    VIP_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Multiple Configurations exist")
    VIP_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats estimated")

    __table_args__ = (
        Index(
            "ux_target_aircraft_reg_sn",
            Registration,
            Serial_Number,
            unique=True,
        ),
    )


class CiriumAircraftsDeltas(Base):
    source_id: Mapped[int] = mapped_column(
        ForeignKey("ciriumaircraft.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )

    revision_id: Mapped[int] = mapped_column(
        ForeignKey("aircraftrevisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    is_latest: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True
    )

    plan_type: Mapped[str] = mapped_column(String, nullable=True, default=None, name="plan_type")

    Type: Mapped[str] = mapped_column(String, nullable=True, name="Type")
    Serial_Number: Mapped[str] = mapped_column(String, nullable=True, name="Serial Number", index=True)
    Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Manufacturer")
    Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Master Series")
    Registration: Mapped[str] = mapped_column(String, nullable=True, name="Registration", index=True)
    Status: Mapped[str] = mapped_column(String, nullable=True, name="Status")
    Age: Mapped[float] = mapped_column(Float, nullable=True, name="Age")

    # Operator / Owner
    Operator: Mapped[str] = mapped_column(String, nullable=True, name="Operator")
    Manager: Mapped[str] = mapped_column(String, nullable=True, name="Manager")
    Owner: Mapped[str] = mapped_column(String, nullable=True, name="Owner")

    # Engines
    Engine_Type: Mapped[str] = mapped_column(String, nullable=True, name="Engine Type")
    Engine_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Series")

    # Status dates
    Status_Change_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Status Change Date")
    Status_Duration_years: Mapped[float] = mapped_column(Float, nullable=True, name="Status Duration (years)")

    # Insurance
    Hull_Insurance_Placement_Group: Mapped[str] = mapped_column(String, nullable=True, name="Hull Insurance Placement Group")

    # Weights (lbs)
    Certified_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Certified MTOW (lbs)")
    Operating_MTOW_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating MTOW (lbs)")
    Max_Landing_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Landing Weight (lbs)")
    Max_Zero_Fuel_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Zero Fuel Weight (lbs)")
    Operating_Empty_Weight_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Operating Empty Weight (lbs)")
    Max_Payload_lbs: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Payload (lbs)")
    Max_Cargo_Volume_cuft: Mapped[int] = mapped_column(Integer, nullable=True, name="Max Cargo Volume (cubic feet)")

    Fuel_Capacity_USG: Mapped[float] = mapped_column(Float, nullable=True, name="Fuel Capacity (US gallons)")
    Noise_Category: Mapped[str] = mapped_column(String, nullable=True, name="Noise Category")

    Age_at_Retirement_or_Written_Off: Mapped[float] = mapped_column(Float, nullable=True, name="Age at Retirement/Written Off")

    # IDs
    FG_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="FG ID")
    Order_ID: Mapped[int] = mapped_column(Integer, nullable=True, name="Order ID")
    Line_Number: Mapped[str] = mapped_column(String, nullable=True, name="Line Number")
    Block_Number: Mapped[str] = mapped_column(String, nullable=True, name="Block Number")
    Fleet_Number: Mapped[str] = mapped_column(String, nullable=True, name="Fleet Number")

    # Country / Geo
    Country_Subregion_of_Registration: Mapped[str] = mapped_column(String, nullable=True, name="Country/Subregion of Registration")

    # Dates
    First_Flight_Date: Mapped[date] = mapped_column(Date, nullable=True, name="First Flight Date")
    Build_Year: Mapped[int] = mapped_column(Integer, nullable=True, name="Build Year")
    Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Delivery Date")
    In_Service_Date: Mapped[date] = mapped_column(Date, nullable=True, name="In Service Date")
    Order_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Order Date")

    Primary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Primary Usage")
    Secondary_Usage: Mapped[str] = mapped_column(String, nullable=True, name="Secondary Usage")

    Indicative_Market_Value_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Value (US$m)")
    Indicative_Market_Lease_Rate_USm: Mapped[float] = mapped_column(Float, nullable=True, name="Indicative Market Lease Rate (US$m)")

    # Aircraft family info
    Current_Family: Mapped[str] = mapped_column(String, nullable=True, name="Current Family")
    Series: Mapped[str] = mapped_column(String, nullable=True, name="Series")
    Aircraft_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Sub Series")
    Aircraft_Minor_Variant: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Minor Variant")
    Modifiers: Mapped[str] = mapped_column(String, nullable=True, name="Modifiers")

    Number_Of_Engines: Mapped[int] = mapped_column(Integer, nullable=True, name="Number Of Engines")
    Engine_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="Engine Manufacturer")
    Engine_Family: Mapped[str] = mapped_column(String, nullable=True, name="Engine Family")
    Engine_Master_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Master Series")
    Engine_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="Engine Sub Series")
    enginepropulsiontypename: Mapped[str] = mapped_column(String, nullable=True, name="enginepropulsiontypename")

    Market_Sector: Mapped[str] = mapped_column(String, nullable=True, name="Market Sector")
    Market_Class: Mapped[str] = mapped_column(String, nullable=True, name="Market Class")
    Market_Grouping: Mapped[str] = mapped_column(String, nullable=True, name="Market Grouping")

    Soviet_Built: Mapped[bool] = mapped_column(Boolean, nullable=True, name="Soviet Built")

    # Leasing
    Lease_Type: Mapped[str] = mapped_column(String, nullable=True, name="Lease Type")
    Lease_Dry_Wet: Mapped[str] = mapped_column(String, nullable=True, name="Lease Dry / Wet")
    Lease_Start: Mapped[date] = mapped_column(Date, nullable=True, name="Lease Start")
    Lease_End: Mapped[date] = mapped_column(Date, nullable=True, name="Lease End")
    Lease_Duration_months: Mapped[float] = mapped_column(Float, nullable=True, name="Lease Duration (months)")
    Is_Lease_End_Estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Is Lease End Estimated")

    # Base airport
    Base_Airport_Region: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Region")
    Base_Airport_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Country/Subregion")
    Base_Airport_State: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport State")
    Base_Airport_City: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport City")
    Base_Airport_Name: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport Name")
    Base_Airport_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport ICAO")
    Base_Airport_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Base Airport IATA")

    # Build location
    Build_Region: Mapped[str] = mapped_column(String, nullable=True, name="Build Region")
    Build_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Build Country/Subregion")
    Build_State: Mapped[str] = mapped_column(String, nullable=True, name="Build State")
    Build_City: Mapped[str] = mapped_column(String, nullable=True, name="Build City")
    Build_Location: Mapped[str] = mapped_column(String, nullable=True, name="Build Location")
    Build_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Build ICAO")
    Build_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Build IATA")

    # Trust owner
    Trust_Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Region")
    Trust_Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Country/Subregion")
    Trust_Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner State")
    Trust_Owner: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner")
    Trust_Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Category")
    Trust_Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Type")
    Trust_Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Trust Owner Company Status")

    # "Operated For"
    Operated_For_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Region")
    Operated_For_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Country/Subregion")
    Operated_For_State: Mapped[str] = mapped_column(String, nullable=True, name="Operated For State")
    Operated_For: Mapped[str] = mapped_column(String, nullable=True, name="Operated For")
    Operated_For_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Category")
    Operated_For_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Type")
    Operated_For_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operated For Company Status")

    # Operator group
    Operator_Group_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Region")
    Operator_Group_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Country/Subregion")
    Operator_Group_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group State")
    Operator_Group: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group")
    Operator_Group_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Category")
    Operator_Group_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Type")
    Operator_Group_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Group Company Status")

    # Operational lessor
    Operational_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor")
    Operational_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Region")
    Operational_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Country/Subregion")
    Operational_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor State")
    Operational_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Category")
    Operational_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Type")
    Operational_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operational Lessor Company Status")

    # Sub-lessor
    Sub_Lessor_Region: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Region")
    Sub_Lessor_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Country/Subregion")
    Sub_Lessor_State: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor State")
    Sub_Lessor: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor")
    Sub_Lessor_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Category")
    Sub_Lessor_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Type")
    Sub_Lessor_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Sub Lessor Company Status")

    # Manager
    Manager_Region: Mapped[str] = mapped_column(String, nullable=True, name="Manager Region")
    Manager_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Manager Country/Subregion")
    Manager_State: Mapped[str] = mapped_column(String, nullable=True, name="Manager State")
    Manager_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Category")
    Manager_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Type")
    Manager_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Manager Company Status")

    # Operator
    Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Operator Region")
    Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Operator Country/Subregion")
    Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Operator State")
    Operator_IATA: Mapped[str] = mapped_column(String, nullable=True, name="Operator IATA")
    Operator_ICAO: Mapped[str] = mapped_column(String, nullable=True, name="Operator ICAO")
    Operator_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Category")
    Operator_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Type")
    Operator_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Operator Company Status")
    Operator_Delivery_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Operator Delivery Date")
    Duration_With_Operator_months: Mapped[float] = mapped_column(Float, nullable=True, name="Duration With Operator (months)")

    # Original operator
    Original_Operator_Region: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Region")
    Original_Operator_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Country/Subregion")
    Original_Operator_State: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator State")
    Original_Operator: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator")
    Original_Operator_Category: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Category")
    Original_Operator_Type: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Type")
    Original_Operator_Status: Mapped[str] = mapped_column(String, nullable=True, name="Original Operator Status")

    # Owner
    Owner_Region: Mapped[str] = mapped_column(String, nullable=True, name="Owner Region")
    Owner_Country_Subregion: Mapped[str] = mapped_column(String, nullable=True, name="Owner Country/Subregion")
    Owner_State: Mapped[str] = mapped_column(String, nullable=True, name="Owner State")
    Owner_Company_Category: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Category")
    Owner_Company_Type: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Type")
    Owner_Company_Status: Mapped[str] = mapped_column(String, nullable=True, name="Owner Company Status")

    Participants: Mapped[str] = mapped_column(String, nullable=True, name="Participants")

    # APU
    APU_Manufacturer: Mapped[str] = mapped_column(String, nullable=True, name="APU Manufacturer")
    APU_Type: Mapped[str] = mapped_column(String, nullable=True, name="APU Type")
    APU_Sub_Series: Mapped[str] = mapped_column(String, nullable=True, name="APU Sub Series")

    # Seats & cabin
    Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats")

    # Business, First, Premium, Economy, VIP, Utility
    Economy_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Cabin Name")
    Economy_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet Model")
    Economy_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Internet OEM")
    Economy_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Converted Seats")
    Economy_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Convertible Seats")
    Economy_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Number of Seats")
    Economy_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Paid Connectivity")
    Economy_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone Model")
    Economy_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Phone OEM")
    Economy_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Power Outlet")
    Economy_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE Model")
    Economy_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Primary IFE OEM")
    Economy_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Primary IFE Screen Size (in)")
    Economy_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Model")
    Economy_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat OEM")
    Economy_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Pitch (in)")
    Economy_Class_Seat_Recline_deg: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (deg)")
    Economy_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Recline (in)")
    Economy_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Abreast")
    Economy_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seats Converted To Class")
    Economy_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Economy Class Seat Support OEM")
    Economy_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Economy Class Seat Width (in)")

    Business_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Cabin Name")
    Business_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet Model")
    Business_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Internet OEM")
    Business_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Converted Seats")
    Business_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Convertible Seats")
    Business_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats")
    Business_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Paid Connectivity")
    Business_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone Model")
    Business_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Phone OEM")
    Business_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Power Outlet")
    Business_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE Model")
    Business_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Primary IFE OEM")
    Business_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Primary IFE Screen Size (in)")
    Business_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Model")
    Business_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat OEM")
    Business_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Pitch (in)")
    Business_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Seat Recline (deg)")
    Business_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Recline (in)")
    Business_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Abreast")
    Business_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seats Converted To Class")
    Business_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Business Class Seat Support OEM")
    Business_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Business Class Seat Width (in)")

    Other_Utility_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Cabin Name")
    Other_Utility_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet Model")
    Other_Utility_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Internet OEM")
    Other_Utility_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Converted Seats")
    Other_Utility_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Convertible Seats")
    Other_Utility_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats")
    Other_Utility_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Paid Connectivity")
    Other_Utility_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone Model")
    Other_Utility_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Phone OEM")
    Other_Utility_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Power Outlet")
    Other_Utility_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE Model")
    Other_Utility_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Primary IFE OEM")
    Other_Utility_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Primary IFE Screen Size (in)")
    Other_Utility_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Model")
    Other_Utility_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat OEM")
    Other_Utility_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Pitch (in)")
    Other_Utility_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Seat Recline (deg)")
    Other_Utility_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Recline (in)")
    Other_Utility_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seats Abreast")
    Other_Utility_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Other Utility Seats Converted To Class")
    Other_Utility_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Other/Utility Seat Support OEM")
    Other_Utility_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Other/Utility Seat Width (in)")

    First_Class_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="First Class Cabin Name")
    First_Class_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet Model")
    First_Class_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Internet OEM")
    First_Class_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Converted Seats")
    First_Class_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Convertible Seats")
    First_Class_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats")
    First_Class_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="First Class Paid Connectivity")
    First_Class_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone Model")
    First_Class_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Phone OEM")
    First_Class_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="First Class Power Outlet")
    First_Class_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE Model")
    First_Class_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Primary IFE OEM")
    First_Class_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Primary IFE Screen Size (in)")
    First_Class_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Model")
    First_Class_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat OEM")
    First_Class_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Pitch (in)")
    First_Class_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Seat Recline (deg)")
    First_Class_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Recline (in)")
    First_Class_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Abreast")
    First_Class_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seats Converted To Class")
    First_Class_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="First Class Seat Support OEM")
    First_Class_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="First Class Seat Width (in)")

    Premium_Economy_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Cabin Name")
    Premium_Economy_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet Model")
    Premium_Economy_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Internet OEM")
    Premium_Economy_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Converted Seats")
    Premium_Economy_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Convertible Seats")
    Premium_Economy_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats")
    Premium_Economy_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Paid Connectivity")
    Premium_Economy_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone Model")
    Premium_Economy_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Phone OEM")
    Premium_Economy_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Power Outlet")
    Premium_Economy_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE Model")
    Premium_Economy_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Primary IFE OEM")
    Premium_Economy_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Primary IFE Screen Size (in)")
    Premium_Economy_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Model")
    Premium_Economy_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat OEM")
    Premium_Economy_Seat_Pitch_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Pitch (in)")
    Premium_Economy_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Seat Recline (deg)")
    Premium_Economy_Seat_Recline_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Recline (in)")
    Premium_Economy_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Abreast")
    Premium_Economy_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seats Converted To Class")
    Premium_Economy_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="Premium Economy Seat Support OEM")
    Premium_Economy_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="Premium Economy Seat Width (in)")

    VIP_Cabin_Name: Mapped[str] = mapped_column(String, nullable=True, name="VIP Cabin Name")
    VIP_Internet_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet Model")
    VIP_Internet_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Internet OEM")
    VIP_Number_of_Converted_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Converted Seats")
    VIP_Number_of_Convertible_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Convertible Seats")
    VIP_Number_of_Seats: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats")
    VIP_Paid_Connectivity: Mapped[str] = mapped_column(String, nullable=True, name="VIP Paid Connectivity")
    VIP_Phone_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone Model")
    VIP_Phone_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Phone OEM")
    VIP_Power_Outlet: Mapped[str] = mapped_column(String, nullable=True, name="VIP Power Outlet")
    VIP_Primary_IFE_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE Model")
    VIP_Primary_IFE_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Primary IFE OEM")
    VIP_Primary_IFE_Screen_Size_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Primary IFE Screen Size (in)")
    VIP_Seat_Model: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Model")
    VIP_Seat_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat OEM")
    VIP_Seat_Pitch_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Pitch (in)")
    VIP_Seat_Recline_deg: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (deg)")
    VIP_Seat_Recline_in: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Seat Recline (in)")
    VIP_Seats_Abreast: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Abreast")
    VIP_Seats_Converted_To_Class: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seats Converted To Class")
    VIP_Seat_Support_OEM: Mapped[str] = mapped_column(String, nullable=True, name="VIP Seat Support OEM")
    VIP_Seat_Width_in: Mapped[float] = mapped_column(Float, nullable=True, name="VIP Seat Width (in)")

    # Flight hours & cycles
    Cumulative_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours")
    Cumulative_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles")
    Reported_Hours_and_Cycles_Date: Mapped[date] = mapped_column(Date, nullable=True, name="Reported Hours and Cycles Date")

    Average_Flight_Time: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time")
    Average_Annual_Cycles: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Cycles")
    Average_Annual_Hours: Mapped[float] = mapped_column(Float, nullable=True, name="Average Annual Hours")

    Previous_Month_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Cycles")
    Previous_Month_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous Month Hours")

    Previous_12_Months_Cycles: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Cycles")
    Previous_12_Months_Hours: Mapped[int] = mapped_column(Integer, nullable=True, name="Previous 12 Months Hours")

    Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Average Daily Utilisation")
    Previous_12_Months_Average_Daily_Utilisation: Mapped[float] = mapped_column(Float, nullable=True, name="Previous 12 Months Average Daily Utilisation")

    Cumulative_Hours_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Hours With Operator")
    Cumulative_Cycles_With_Operator: Mapped[int] = mapped_column(Integer, nullable=True, name="Cumulative Cycles With Operator")
    Average_Flight_Time_With_Operator: Mapped[float] = mapped_column(Float, nullable=True, name="Average Flight Time With Operator")

    # Storage / conversion location
    Storage_Conversion_Location_Region_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Region Name")
    Storage_Conversion_Location_Country_Subregion_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Country/Subregion Name")
    Storage_Conversion_Location_State_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location State Name")
    Storage_Conversion_Location_City_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location City Name")
    Storage_Conversion_Location_Name: Mapped[str] = mapped_column(String, nullable=True, name="Storage Conversion Location Name")

    Aircraft_Class: Mapped[str] = mapped_column(String, nullable=True, name="Aircraft Class")

    Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Number of Seats estimated")

    # Multi-configuration flags
    Business_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Multiple Configurations exist")
    Business_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Business Class Number of Seats estimated")

    Economy_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Economy Class Multiple Configurations exist")
    Economy_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer,  nullable=True, name="Economy Class Number of Seats estimated")

    First_Class_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Multiple Configurations exist")
    First_Class_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="First Class Number of Seats estimated")

    Other_Utility_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Multiple Configurations exist")
    Other_Utility_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Other/Utility Number of Seats estimated")

    Premium_Economy_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Multiple Configurations exist")
    Premium_Economy_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="Premium Economy Number of Seats estimated")

    VIP_Multiple_Configurations_exist: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Multiple Configurations exist")
    VIP_Number_of_Seats_estimated: Mapped[int] = mapped_column(Integer, nullable=True, name="VIP Number of Seats estimated")
