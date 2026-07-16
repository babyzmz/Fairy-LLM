const MAX_BACKDROP_WIDTH = 1_024;
const MAX_BACKDROP_HEIGHT = 512;

export class ReusableBackdropTextureBuffer {
  private pixels = new Uint8Array(4);
  private pixelWidth = 1;
  private pixelHeight = 1;

  get data(): Uint8Array {
    return this.pixels;
  }

  get width(): number {
    return this.pixelWidth;
  }

  get height(): number {
    return this.pixelHeight;
  }

  write(rgba: Uint8Array, width: number, height: number): boolean {
    const expectedBytes = width * height * 4;
    if (
      !Number.isInteger(width) ||
      !Number.isInteger(height) ||
      width < 1 ||
      height < 1 ||
      width > MAX_BACKDROP_WIDTH ||
      height > MAX_BACKDROP_HEIGHT ||
      rgba.byteLength !== expectedBytes
    ) {
      throw new Error("PRESENCE_BACKDROP_TEXTURE_INVALID");
    }

    const resized = width !== this.pixelWidth || height !== this.pixelHeight;
    if (resized) {
      this.pixels = new Uint8Array(expectedBytes);
      this.pixelWidth = width;
      this.pixelHeight = height;
    }
    this.pixels.set(rgba);
    return resized;
  }
}
