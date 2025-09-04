import os, sys
# Ensure project root on path when running directly
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio, base64, json
from mcp_client.client import MCPClient
from utils.auth_utils import UserRole

async def main():
    client = MCPClient()
    await client.initialize(UserRole.ADMIN)
    try:
        path = os.path.join(os.getcwd(), 'mall_customers.csv')
        if not os.path.exists(path):
            print('Sample file mall_customers.csv not found; skipping upload.')
            return
        with open(path,'rb') as f:
            content = f.read()
        encoded = base64.b64encode(content).decode('utf-8')
        args = {
            'request': {
                'filename': 'mall_customers.csv',
                'content': encoded,
                'content_type': 'text/csv',
                'user_id': 'ui_user',
                'metadata': {'source':'e2e','note':'terminal upload'}
            }
        }
        res = await client.call_tool('upload_file', args, user_role=UserRole.ADMIN)
        print('UPLOAD_RES', json.dumps(res, default=str)[:500])
        file_id = (res or {}).get('file_id')
        if not file_id:
            print('No file_id in response, aborting.')
            return
        inspect_args = {'request': {'file_id': file_id, 'sample_rows': 5, 'user_id': 'ui_user'}}
        insp = await client.call_tool('data_inspect', inspect_args, user_role=UserRole.ADMIN)
        print('INSPECT_RES', json.dumps(insp, default=str)[:500])
        result = await client.process_query(
            f"Perform clustering analysis on dataset with file ID: {file_id}",
            user_id='ui_user',
            user_role=UserRole.ADMIN
        )
        print('CLUSTER_ANALYZE', str(result)[:800])
    finally:
        await client.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
