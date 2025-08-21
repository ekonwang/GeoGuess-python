COMMIT=$(git rev-parse HEAD)
git switch dev
git cherry-pick $COMMIT