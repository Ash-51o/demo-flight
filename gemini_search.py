from google import genai
from google.genai import types

client = genai.Client()

grounding_tool = types.Tool(
    google_search=types.GoogleSearch()
)

config = types.GenerateContentConfig(
    tools=[grounding_tool]
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Find me the list of all the Base Manager and Director of Maintenance in the US Airline industry so that I can offer them aircraft maintainence services",
    config=config,
)

print(response.text)