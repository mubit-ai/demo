"""
MuBit + Google ADK Example: Travel Planning Multi-Agent

A SequentialAgent orchestrates 3 specialized agents to plan a trip:
  - Flight Finder: searches for flights using a tool
  - Hotel Finder: finds accommodations using a tool
  - Itinerary Planner: combines results into a complete itinerary

Uses Gemini natively. MuBit memory service stores each agent's findings
and provides cross-agent/cross-session context.

Requirements:
    pip install -r requirements.txt

Environment variables:
    GOOGLE_API_KEY   - Google/Gemini API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import asyncio
import json
import os
import sys

# Add the SDK and integrations to the path for local development
_REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for p in [os.path.join(_REPO, "sdk", "python", "mubit-sdk", "src"), os.path.join(_REPO, "integrations", "python")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from mubit_adk import MubitMemoryService


# -- Tool functions (realistic static data) --

def search_flights(origin: str, destination: str, date: str) -> str:
    """Search for available flights between cities on a given date.

    Args:
        origin: Departure city (e.g., "San Francisco")
        destination: Arrival city (e.g., "Tokyo")
        date: Travel date (e.g., "2026-04-20")

    Returns:
        Available flight options with prices and schedules.
    """
    flights = {
        ("san francisco", "tokyo"): [
            {"airline": "United Airlines", "flight": "UA837", "depart": "11:30 AM", "arrive": "3:30 PM +1", "duration": "11h 00m", "price": "$892", "class": "Economy", "stops": "Nonstop"},
            {"airline": "ANA", "flight": "NH107", "depart": "5:45 PM", "arrive": "9:55 PM +1", "duration": "11h 10m", "price": "$1,045", "class": "Economy", "stops": "Nonstop"},
            {"airline": "JAL", "flight": "JL001", "depart": "1:00 PM", "arrive": "5:15 PM +1", "duration": "11h 15m", "price": "$978", "class": "Economy", "stops": "Nonstop"},
        ],
    }

    key = (origin.lower().strip(), destination.lower().strip())
    options = flights.get(key, flights.get(("san francisco", "tokyo")))

    result = f"Available flights from {origin} to {destination} on {date}:\n\n"
    for f in options:
        result += (
            f"  {f['airline']} {f['flight']}\n"
            f"    Departs: {f['depart']} → Arrives: {f['arrive']} ({f['duration']})\n"
            f"    {f['class']} | {f['stops']} | {f['price']}\n\n"
        )
    return result


def search_hotels(city: str, checkin: str, checkout: str) -> str:
    """Search for available hotels in a city for given dates.

    Args:
        city: City name (e.g., "Tokyo")
        checkin: Check-in date (e.g., "2026-04-20")
        checkout: Check-out date (e.g., "2026-04-25")

    Returns:
        Available hotel options with prices and ratings.
    """
    hotels = [
        {"name": "Hotel Gracery Shinjuku", "district": "Shinjuku", "rating": 4.3, "price": "$145/night", "total": "$725", "highlights": "Famous Godzilla statue, near Kabukicho, excellent transit access"},
        {"name": "Park Hotel Tokyo", "district": "Shiodome", "rating": 4.5, "price": "$198/night", "total": "$990", "highlights": "Art-themed rooms, Tokyo Tower views, Ginza walkable"},
        {"name": "Hoshinoya Tokyo", "district": "Otemachi", "rating": 4.8, "price": "$410/night", "total": "$2,050", "highlights": "Traditional ryokan luxury, onsen bath, Michelin-level dining"},
    ]

    result = f"Available hotels in {city} ({checkin} to {checkout}):\n\n"
    for h in hotels:
        result += (
            f"  {h['name']} ({h['district']})\n"
            f"    Rating: {h['rating']}/5 | {h['price']} ({h['total']} total)\n"
            f"    Highlights: {h['highlights']}\n\n"
        )
    return result


# -- Constants --

APP_NAME = "travel-planner"
USER_ID = "demo-user"


async def main():
    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY")

    if not google_key:
        print("Error: GOOGLE_API_KEY environment variable is required.")
        sys.exit(1)

    # --- Set up MuBit memory service ---
    mubit_memory = MubitMemoryService(endpoint=endpoint, api_key=api_key)

    # --- Define ADK Agents ---
    flight_finder = LlmAgent(
        name="flight_finder",
        model="gemini-2.0-flash",
        description="Finds the best flights for the trip",
        instruction=(
            "You are a flight search specialist. When given a travel request, "
            "use the search_flights tool to find available flights. Analyze the "
            "options and recommend the best one based on price, schedule, and "
            "convenience. Present your recommendation clearly."
        ),
        tools=[search_flights],
        output_key="flight_results",
    )

    hotel_finder = LlmAgent(
        name="hotel_finder",
        model="gemini-2.0-flash",
        description="Finds the best hotel accommodations",
        instruction=(
            "You are a hotel search specialist. When given a travel request, "
            "use the search_hotels tool to find available accommodations. "
            "Consider the traveler's needs, budget, and location preferences. "
            "Recommend the best option with reasoning."
        ),
        tools=[search_hotels],
        output_key="hotel_results",
    )

    itinerary_planner = LlmAgent(
        name="itinerary_planner",
        model="gemini-2.0-flash",
        description="Creates a complete day-by-day travel itinerary",
        instruction=(
            "You are an expert travel planner. Using the flight and hotel "
            "information from previous agents (available in the conversation), "
            "create a complete day-by-day itinerary. Include:\n"
            "1. Flight details and airport transfer\n"
            "2. Hotel check-in/out information\n"
            "3. Daily activity suggestions with specific locations\n"
            "4. Restaurant recommendations for each day\n"
            "5. Estimated daily budget\n"
            "6. Practical tips (transit passes, cultural notes, etc.)"
        ),
        output_key="final_itinerary",
    )

    # Orchestrate with SequentialAgent
    travel_coordinator = SequentialAgent(
        name="travel_coordinator",
        description="Coordinates the full travel planning pipeline",
        sub_agents=[flight_finder, hotel_finder, itinerary_planner],
    )

    # --- Set up Runner ---
    session_service = InMemorySessionService()
    runner = Runner(
        agent=travel_coordinator,
        app_name=APP_NAME,
        session_service=session_service,
        memory_service=mubit_memory,
    )

    # Create session
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
    )

    print(f"{'='*60}")
    print("  Running Travel Planning Pipeline")
    print(f"  Session: {session.id}")
    print(f"{'='*60}\n")

    # --- Run the pipeline ---
    user_message = "Plan a 5-day trip from San Francisco to Tokyo, departing April 20th 2026. I prefer a mid-range budget."

    print(f"User: {user_message}\n")

    from google.genai import types as genai_types

    user_content = genai_types.Content(
        parts=[genai_types.Part(text=user_message)],
        role="user",
    )

    final_text = ""
    async for event in runner.run_async(
        session_id=session.id,
        user_id=USER_ID,
        new_message=user_content,
    ):
        # Print agent events as they happen
        if hasattr(event, "author") and hasattr(event, "content"):
            author = getattr(event, "author", "unknown")
            text = ""
            if hasattr(event.content, "parts"):
                text = " ".join(
                    p.text for p in event.content.parts
                    if hasattr(p, "text") and p.text
                )
            if text:
                print(f"[{author}] {text[:200]}{'...' if len(text) > 200 else ''}")
                final_text = text

    # --- Post-run MuBit operations ---
    print(f"\n{'='*60}")
    print("  Post-Run: MuBit Memory Operations")
    print(f"{'='*60}\n")

    # Register agents
    for agent_id, role in [
        ("flight_finder", "flight-search"),
        ("hotel_finder", "hotel-search"),
        ("itinerary_planner", "itinerary-creation"),
    ]:
        await mubit_memory.register_agent(
            user_id=USER_ID,
            session_id=session.id,
            agent_id=agent_id,
            role=role,
        )
    print("Agents registered.")

    # Checkpoint
    await mubit_memory.checkpoint(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session.id,
        snapshot="Travel plan complete: SF→Tokyo, 5 days, mid-range budget.",
        label="plan-complete",
    )
    print("Checkpoint created.")

    # Record outcome
    try:
        await mubit_memory.record_outcome(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session.id,
            reference_id=f"trip-plan-{session.id}",
            outcome="success",
            rationale="Complete itinerary generated with flights, hotel, and daily activities.",
        )
        print("Outcome recorded.")
    except Exception as e:
        print(f"Outcome recording note: {e}")

    # Surface strategies
    try:
        strategies = await mubit_memory.surface_strategies(session_id=session.id)
        print(f"Strategies: {strategies}")
    except Exception as e:
        print(f"Strategy surfacing note: {e}")

    # --- Print final itinerary ---
    print(f"\n{'='*60}")
    print("  Final Travel Itinerary")
    print(f"{'='*60}\n")
    print(final_text or "(see agent output above)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
