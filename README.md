# Network Programming Homework 3
## prerequest
- pull this repo
- install uv
```bash
# on linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# on windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
Or you can goto [UV installation page](https://docs.astral.sh/uv/getting-started/installation/) to find your favorite method

- change your working directory into this repo

# setup 
There are four folder inside the `src` file. For each folder, go inside and copy `.env example`, rename it into `.env`, fill up the data you need.

>[!Note]Note
> If this is Network programming demo, we don't need to setup because the default values have already setup.

Just in case, you can run `uv sync` at the root of the repo.

## usage
1. Enter the root of this repo
2. run database first
```bash
uv run src/database/DBserver.py
```
3. run lobby.py
```bash
uv run src/servers/lobby.py
```
4. run developer_server.py
```bash
uv run src/servers/developer_server.py
```
>[!Caution]Caution
>lobby and developer_server must run on the same machine
5. run client2.0.py
```bash
uv run src/client/client2.0.py
```
6. run developer_client.py
```bash
uv run src/developer_client/developer_client.py
```