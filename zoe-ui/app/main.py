import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

static_dir = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=static_dir), name='static')

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))

@app.get('/', response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse('zoe_complete_ui.html', {'request': request})

@app.get('/calendar', response_class=HTMLResponse)
def calendar(request: Request):
    return templates.TemplateResponse('calendar.html', {'request': request})


@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
async def proxy(request: Request, path: str):
    url = f"http://zoe-core:8000/api/{path}"
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            request.method,
            url,
            params=request.query_params,
            content=await request.body(),
            headers={k: v for k, v in request.headers.items() if k.lower() != 'host'}
        )
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
