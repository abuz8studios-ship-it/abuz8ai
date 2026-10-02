import onnxruntime as ort, numpy as np, sys
from PIL import Image
src,out=sys.argv[1],sys.argv[2]
im=Image.open(src).convert('RGB'); W,H=im.size
# model input: multiple of 14, ~518 short side
s=518/min(W,H); w=int(round(W*s/14))*14; h=int(round(H*s/14))*14
x=np.asarray(im.resize((w,h),Image.BICUBIC),dtype=np.float32)/255.
x=(x-[0.485,0.456,0.406])/[0.229,0.224,0.225]
x=x.transpose(2,0,1)[None].astype(np.float32)
sess=ort.InferenceSession('da.onnx')
d=sess.run(None,{'pixel_values':x})[0][0]
d=(d-d.min())/(d.max()-d.min())
Image.fromarray((d*255).astype(np.uint8)).resize((W,H),Image.BICUBIC).save(out)
print('ok',W,H,d.shape)
