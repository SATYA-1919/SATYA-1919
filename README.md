name: Generate Snake

on:
  schedule:
    - cron: "0 0 * * *"   # daily refresh
  workflow_dispatch:        # lets you run it manually from the Actions tab
  push:
    branches:
      - main

permissions:
  contents: write

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - name: Generate snake SVGs
        uses: Platane/snk@v3
        with:
          github_user_name: ${{ github.repository_owner }}
          outputs: |
            dist/github-contribution-grid-snake-dark.svg?palette=github-dark&color_snake=1F6FEB&color_dots=0D1117,1F6FEB,58A6FF,58A6FF,ffffff
            dist/github-contribution-grid-snake.svg?color_snake=1F6FEB&color_dots=ebedf0,1F6FEB,58A6FF,1F6FEB,0D1117

      - name: Push to output branch
        uses: crazy-max/ghaction-github-pages@v4
        with:
          target_branch: output
          build_dir: dist
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
