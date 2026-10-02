import numpy as np, struct
from PIL import Image
W,H=512,768
rgb=np.asarray(Image.open('city.jpg').convert('RGB').resize((W,H),Image.LANCZOS)).astype(np.uint8)
d=np.asarray(Image.open('city_depth.png').convert('L').resize((W,H),Image.BICUBIC)).astype(np.float32)/255.
z=1.0/(0.33+0.95*d)                     # disparity -> metric-ish depth
f=0.9*H                                 # focal (px)
u,v=np.meshgrid(np.arange(W),np.arange(H))
X=(u-W/2)*z/f; Y=-(v-H/2)*z/f; Z=-z
# drop splats on depth cliffs (rubber-sheet artifacts)
gy,gx=np.gradient(z); cliff=np.hypot(gx,gy)>0.05
keep=~cliff
P=np.stack([X,Y,Z],-1)[keep]; C=rgb[keep]; S=(z/f)[keep]
# background plate: inpaint what the foreground (hero + HUD) hides, so orbiting never shows holes
import cv2
fg=(d>0.42).astype(np.uint8)*255
fg=cv2.dilate(fg,np.ones((9,9),np.uint8))
bgrgb=cv2.inpaint(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),fg,9,cv2.INPAINT_TELEA)
bgrgb=cv2.cvtColor(bgrgb,cv2.COLOR_BGR2RGB)
dbg=d.copy(); dbg[fg>0]=0
dbg=cv2.inpaint((dbg*255).astype(np.uint8),fg,15,cv2.INPAINT_TELEA).astype(np.float32)/255.
zb=1.0/(0.33+0.95*np.minimum(dbg,0.38))
m=fg>0
Pb=np.stack([((u-W/2)*zb/f)[m],(-(v-H/2)*zb/f)[m],(-zb)[m]],-1)
P=np.concatenate([P,Pb]);C=np.concatenate([C,bgrgb[m]]);S=np.concatenate([S,(zb/f)[m]])
print('splats',len(P))
lo=P.min(0);hi=P.max(0)
q=np.round((P-lo)/(hi-lo)*65535).astype('<u2')
s=np.clip(np.round(S/S.max()*255),1,255).astype(np.uint8)
buf=np.concatenate([q.view(np.uint8).reshape(-1,6),C,s[:,None]],1).astype(np.uint8)  # 10 bytes/splat
hdr=struct.pack('<4sI7f',b'AZ8S',len(P),*lo,*hi,float(S.max()))
open('site/img/city.az8s','wb').write(hdr+buf.tobytes())
# standard 3D Gaussian Splatting PLY (opens in SuperSplat / Postshot / any 3DGS viewer)
SH0=0.28209479177387814
fdc=(C/255.-0.5)/SH0
sc=np.log(np.maximum(S*0.9,1e-6))
n=len(P)
dt=np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('f_dc_0','<f4'),('f_dc_1','<f4'),('f_dc_2','<f4'),('opacity','<f4'),('scale_0','<f4'),('scale_1','<f4'),('scale_2','<f4'),('rot_0','<f4'),('rot_1','<f4'),('rot_2','<f4'),('rot_3','<f4')])
a=np.zeros(n,dt)
a['x'],a['y'],a['z']=P[:,0],P[:,1],P[:,2]
a['f_dc_0'],a['f_dc_1'],a['f_dc_2']=fdc[:,0],fdc[:,1],fdc[:,2]
a['opacity']=4.0; a['scale_0']=a['scale_1']=sc; a['scale_2']=sc-1.0; a['rot_0']=1.0
head='ply\nformat binary_little_endian 1.0\nelement vertex %d\n'%n+''.join('property float %s\n'%k for k in dt.names)+'end_header\n'
import os; os.makedirs('splat',exist_ok=True)
open('splat/abuz8_city.ply','wb').write(head.encode()+a.tobytes())
print(os.path.getsize('site/img/city.az8s'),os.path.getsize('splat/abuz8_city.ply'))
