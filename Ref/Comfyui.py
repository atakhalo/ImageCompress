    def _extract_png_metadata(self, png_image):
        """
        Extract ComfyUI workflow metadata from PNG image.

        Args:
            png_image: PIL Image object

        Returns:
            dict: Metadata containing 'prompt' and 'workflow' if available
        """
        metadata = {}

        try:
            # Extract PNG text chunks
            info = png_image.info

            if "prompt" in info:
                try:
                    metadata["prompt"] = json.loads(info["prompt"])
                except json.JSONDecodeError:
                    metadata["prompt"] = info["prompt"]

            if "workflow" in info:
                try:
                    metadata["workflow"] = json.loads(info["workflow"])
                except json.JSONDecodeError:
                    metadata["workflow"] = info["workflow"]

        except Exception as e:
            print(f"[Compress API] Error extracting PNG metadata: {e}")

        return metadata

    def _create_exif_with_metadata(self, metadata):
        """
        Create EXIF bytes with ComfyUI metadata embedded.
        Uses ComfyUI-Image-Saver methodology.

        Args:
            metadata: Dict with 'prompt' and 'workflow' keys

        Returns:
            tuple: (exif_bytes, metadata_status) where status is 'full', 'workflow_only', or 'none'
        """
        if not PIEXIF_AVAILABLE:
            return piexif.dump({}), 'none'

        try:
            # Prepare metadata for EXIF embedding
            exif_dict = {"0th": {}, "Exif": {}}
            metadata_status = 'none'

            # Try to embed workflow
            if "workflow" in metadata:
                workflow_str = f"workflow:{json.dumps(metadata['workflow'], separators=(',', ':'))}"
                exif_dict["0th"][piexif.ImageIFD.Make] = workflow_str.encode('utf-8')
                metadata_status = 'workflow_only'

            # Try to embed prompt
            if "prompt" in metadata:
                prompt_str = f"prompt:{json.dumps(metadata['prompt'], separators=(',', ':'))}"
                exif_dict["0th"][piexif.ImageIFD.Model] = prompt_str.encode('utf-8')
                if metadata_status == 'workflow_only':
                    metadata_status = 'full'

            # Create EXIF bytes
            exif_bytes = piexif.dump(exif_dict)

            # Check size limit and apply fallback strategy
            if len(exif_bytes) > self.MAX_EXIF_SIZE:
                print(f"[Compress API] Metadata too large ({len(exif_bytes)} bytes), removing prompt")
                # Fallback 1: Remove prompt, keep workflow only
                exif_dict["0th"].pop(piexif.ImageIFD.Model, None)
                exif_bytes = piexif.dump(exif_dict)
                metadata_status = 'workflow_only'

                if len(exif_bytes) > self.MAX_EXIF_SIZE:
                    print(f"[Compress API] Workflow still too large ({len(exif_bytes)} bytes), cannot embed")
                    # Fallback 2: Remove everything
                    exif_bytes = piexif.dump({})
                    metadata_status = 'none'

            return exif_bytes, metadata_status

        except Exception as e:
            print(f"[Compress API] Error creating EXIF metadata: {e}")
            return piexif.dump({}), 'none'
