# Enable GitHub Pages

GitHub Pages must be enabled after the documentation commits reach `main`.

1. Open `https://github.com/SuryanshSS1011/Exhibit-A`.
2. Select **Settings**.
3. Select **Pages** under **Code and automation**.
4. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
5. Set **Branch** to `main` and set the folder to `/docs`.
6. Select **Save**.
7. Wait for GitHub to finish the first deployment.
8. Open `https://suryanshss1011.github.io/Exhibit-A/`.

No Actions workflow is required. The `main` branch and `/docs` folder are the complete
publishing source. The Jekyll exclusion list keeps the Detective verification runbook,
the internal roadmap file, and this repository administration guide out of the site.

If the site returns a temporary 404 immediately after saving, wait for the deployment
to finish and refresh the page. Future commits to `main` that change `/docs` will trigger
a new Pages build automatically.
