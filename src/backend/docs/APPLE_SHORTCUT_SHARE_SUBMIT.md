# Apple Shortcut Share Submit

Use this to send URLs from the iPhone share sheet straight into the manual submit flow.

## Backend contract

- Endpoint: `POST /api/v1/admin/share-target/submit`
- Header: `X-Admin-Share-Token: <your scoped token>`
- Body:

```json
{
  "url": "https://example.com/story",
  "importance_level": 0
}
```

- Success response includes:
  - `content_id`
  - `duplicate`
  - `status`
  - `message`
  - `content_type` (`ARTICLE`, `VIDEO`, or `REEL`)

## Required secret

Set `ADMIN_SHARE_TOKEN` on the backend. This token is separate from `ADMIN_API_KEY` and should be scoped only to the share-target endpoint.

If `ADMIN_SHARE_TOKEN` is unset, the endpoint fails closed with `401`.

## Apple Shortcut setup

Create a new Shortcut with these actions:

1. Enable `Show in Share Sheet`.
2. Accept `URLs` and `Text` from the share sheet.
3. Add `Get URLs from Input`.
4. Add `Get Item from List` and choose `First Item`.
5. Add `Get Contents of URL` with:
   - Method: `POST`
   - URL: `https://<your-backend-host>/api/v1/admin/share-target/submit`
   - Headers:
     - `Content-Type: application/json`
     - `X-Admin-Share-Token: <your scoped token>`
   - Request Body: JSON
     - `url`: `First Item from List`
     - `importance_level`: `0`
6. Add `Get Dictionary Value` for `message` from the response body.
7. Add `Show Notification` using that message.

## Notes

- YouTube watch URLs and Shorts URLs are accepted.
- YouTube submissions are created as real `VIDEO` or `REEL` items when metadata is available.
- If YouTube metadata lookup fails, the backend still creates a thin manual stub so the share does not fail.
- No `blips-mobile` changes are required for v1.
