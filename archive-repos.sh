#!/bin/bash

# Archive all repositories older than 9 months
# Run this script with: bash archive-repos.sh

echo "Starting to archive old repositories..."
echo ""

# Array of repository names to archive
repos=(
  "SUPERSTORE-SALES-MANAGEMENT-SYSTEM-DBMS-PROJECT"
  "-job-portal"
  "first-repository"
  "project-find-ashwin"
  "Food-Delivery-App-From-Scratch"
  "GPTs"
  "practice-"
  "bin"
  "KS035"
  "git-session"
  "arijitparia2002"
  "github-profile-readme-generator"
  "JOB-PORTAL-HACKATHON"
  "PR-NETFLIX"
  "crypto_scrapping"
  "Smoothie_app"
)

# Counter for tracking progress
count=0
total=${#repos[@]}

# Archive each repository
for repo in "${repos[@]}"; do
  ((count++))
  echo "[$count/$total] Archiving: $repo"
  gh repo archive "aarif-123/$repo" --confirm 2>/dev/null
  if [ $? -eq 0 ]; then
    echo "✓ Successfully archived: $repo"
  else
    echo "✗ Failed to archive: $repo (may already be archived or doesn't exist)"
  fi
  echo ""
done

echo "Done! All repositories have been processed."
echo "Check your GitHub profile to verify the archived repositories."
