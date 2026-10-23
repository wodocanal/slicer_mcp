import asyncio
import json

from PIL import Image

from slicer_mcp.adapters.dimensional import Slicer3D
from slicer_mcp.models import ModelContext
from slicer_mcp.server import create_server
from slicer_mcp.service import SlicerService


def test_mcp_contract_and_image_content(cube, tmp_path):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (20, 20), "red").save(photo)
    server = create_server(SlicerService([Slicer3D()]))

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools["analyze_model"].annotations.readOnlyHint is True
        assert tools["slice_model"].annotations.readOnlyHint is False
        assert "request" in tools["slice_model"].inputSchema["required"]
        blocks = await server.call_tool(
            "get_model_context",
            {
                "slicer": "base-3d",
                "request": ModelContext(
                    model_path=cube,
                    prompt="Прочный корпус",
                    photo_paths=[photo],
                ).model_dump(mode="json"),
            },
        )
        assert blocks[0].type == "text"
        assert json.loads(blocks[0].text)["prompt"] == "Прочный корпус"
        assert blocks[1].type == "image"
        assert blocks[1].mimeType == "image/jpeg"

    asyncio.run(check())
