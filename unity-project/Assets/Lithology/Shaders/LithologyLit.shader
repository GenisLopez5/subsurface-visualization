Shader "Lithology/URP Lithology Lit"
{
 Properties
 {
  _BaseMap("Macro albedo (sRGB)",2D)="white"{}
  [Normal] _NormalMap("Macro normal Y+",2D)="bump"{}
  _SurfaceMap("Linear R metal G AO A smooth",2D)="white"{}
  _DetailMap("Separate grain color",2D)="gray"{}
  [Normal] _DetailNormal("Separate grain normal",2D)="bump"{}
  _MineralMask("Linear mineral region R light G dark",2D)="gray"{}
  _CoverageMask("Optional host component coverage",2D)="white"{}
  _HasCoverageMask("Has coverage map",Float)=0
  _MineralBalance("Artistic dark region population",Range(0,1))=.3
  _HasMineralRegions("Has separable mineral fields",Float)=0
  _ComponentMap1("Component 1 albedo",2D)="white"{}
  [Normal] _ComponentNormal1("Component 1 normal",2D)="bump"{}
  _ComponentSurface1("Component 1 packed",2D)="white"{}
  _ComponentMap2("Component 2 albedo",2D)="white"{}
  [Normal] _ComponentNormal2("Component 2 normal",2D)="bump"{}
  _ComponentSurface2("Component 2 packed",2D)="white"{}
  _Tint("Palette",Color)=(1,1,1,1)
  _MineralLight("Light mineral",Color)=(.8,.8,.7,1)
  _MineralDark("Dark mineral",Color)=(.1,.12,.13,1)
  _LegendColor("Legend color",Color)=(.5,.5,.5,1)
  _LayerColor1("Component color 1",Color)=(1,1,1,1)
  _LayerColor2("Component color 2",Color)=(1,1,1,1)
  _Layer1("Mask / coverage / spacing / relief 1",Vector)=(0,0,.1,0)
  _Layer2("Mask / coverage / spacing / relief 2",Vector)=(0,0,.1,0)
  _LayerSurface1("Smooth / metal / maps 1",Vector)=(.2,0,0,0)
  _LayerSurface2("Smooth / metal / maps 2",Vector)=(.2,0,0,0)
  _Structure("Bedding / cleavage / fractures / banding",Vector)=(0,0,0,0)
  _Spacing("Bed / cleavage / fracture / mottle metres",Vector)=(.12,.08,.45,.5)
  _Fabric("Welding / sorting / support / mud",Vector)=(0,0,.3,1)
  _GeoOrigin("Geological origin",Vector)=(0,0,0,0)
  _BeddingNormal("Bedding direction",Vector)=(0,1,0,0)
  _CleavageNormal("Independent cleavage direction",Vector)=(1,1,0,0)
  _MacroScale("Macro tile metres",Float)=1
  _GrainScale("Display grain metres",Float)=.02
  _DetailStrength("Detail color strength",Range(0,1))=.4
  _DetailEnabled("Independent detail supported",Float)=1
  _NormalStrength("Relief strength",Range(0,2))=.5
  _HasNormal("Has macro normal",Float)=0
  _HasDetailNormal("Has grain normal",Float)=0
  _HasSurface("Has packed surface",Float)=0
  _InvertGreen("Convert DirectX Y- normal",Float)=0
  _Smoothness("Finish",Range(0,1))=.25
  _WorldSpace("World geological coordinates",Float)=1
  _Triplanar("Triplanar (UV needs tangents)",Float)=1
  _UnitMetres("Metres per Unity unit",Float)=1
  _DisplayScale("Viewer texture density",Float)=1
  _DisplayOrigin("Viewer section anchor",Vector)=(0,0,0,0)
  _VeinWidth("Vein width metres",Float)=.004
  _Irregularity("Bedding irregularity",Range(0,1))=.15
  _Seed("Stable illustrative seed",Float)=1001
  _Legend("Legend mode",Float)=0
  _Schematic("Schematic fallback",Float)=0
  _StatusPattern("Distinct fallback pattern",Float)=0
  _LayerCount("Compiled layer count",Float)=0
  _ProceduralDark("Explicit tar or bitumen",Float)=0
 }
 SubShader
 {
  Tags { "RenderPipeline"="UniversalPipeline" "RenderType"="Opaque" "Queue"="Geometry" }
  HLSLINCLUDE
  #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
  #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"
  TEXTURE2D(_BaseMap); SAMPLER(sampler_BaseMap);
  TEXTURE2D(_NormalMap); SAMPLER(sampler_NormalMap);
  TEXTURE2D(_SurfaceMap); SAMPLER(sampler_SurfaceMap);
  TEXTURE2D(_DetailMap); SAMPLER(sampler_DetailMap);
  TEXTURE2D(_DetailNormal); SAMPLER(sampler_DetailNormal);
  TEXTURE2D(_MineralMask); SAMPLER(sampler_MineralMask);
  TEXTURE2D(_CoverageMask); SAMPLER(sampler_CoverageMask);
  TEXTURE2D(_ComponentMap1); SAMPLER(sampler_ComponentMap1);
  TEXTURE2D(_ComponentNormal1); SAMPLER(sampler_ComponentNormal1);
  TEXTURE2D(_ComponentSurface1); SAMPLER(sampler_ComponentSurface1);
  TEXTURE2D(_ComponentMap2); SAMPLER(sampler_ComponentMap2);
  TEXTURE2D(_ComponentNormal2); SAMPLER(sampler_ComponentNormal2);
  TEXTURE2D(_ComponentSurface2); SAMPLER(sampler_ComponentSurface2);
  CBUFFER_START(UnityPerMaterial)
   float4 _Tint,_MineralLight,_MineralDark,_LegendColor,_LayerColor1,_LayerColor2;
   float4 _Layer1,_Layer2,_LayerSurface1,_LayerSurface2,_Structure,_Spacing,_Fabric;
   float4 _GeoOrigin,_BeddingNormal,_CleavageNormal,_DisplayOrigin;
   float _MacroScale,_GrainScale,_DetailStrength,_DetailEnabled,_NormalStrength,_HasNormal,_HasDetailNormal,_HasSurface,_InvertGreen,_Smoothness;
   float _WorldSpace,_Triplanar,_UnitMetres,_VeinWidth,_Irregularity,_Seed,_Legend,_Schematic,_StatusPattern,_LayerCount,_ProceduralDark;
   float _HasCoverageMask,_MineralBalance,_HasMineralRegions,_DisplayScale;
  CBUFFER_END
  struct Attributes { float4 positionOS:POSITION; float3 normalOS:NORMAL; float4 tangentOS:TANGENT; float2 uv:TEXCOORD0; UNITY_VERTEX_INPUT_INSTANCE_ID };
  struct Varyings { float4 positionCS:SV_POSITION; float3 positionWS:TEXCOORD0; float3 positionOS:TEXCOORD1; float3 normalWS:TEXCOORD2; float4 tangentWS:TEXCOORD3; float2 uv:TEXCOORD4; float fog:TEXCOORD5; UNITY_VERTEX_INPUT_INSTANCE_ID UNITY_VERTEX_OUTPUT_STEREO };
  Varyings Vert(Attributes a)
  {
   Varyings o=(Varyings)0;UNITY_SETUP_INSTANCE_ID(a);UNITY_TRANSFER_INSTANCE_ID(a,o);UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
   o.positionOS=a.positionOS.xyz;o.positionWS=TransformObjectToWorld(a.positionOS.xyz);o.positionCS=TransformWorldToHClip(o.positionWS);
   o.normalWS=TransformObjectToWorldNormal(a.normalOS);o.tangentWS=float4(TransformObjectToWorldDir(a.tangentOS.xyz),a.tangentOS.w*GetOddNegativeScale());o.uv=a.uv;o.fog=ComputeFogFactor(o.positionCS.z);return o;
  }
  float Hash(float3 p) { p=frac(p*.1031+_Seed*.00071);p+=dot(p,p.yzx+33.33);return frac((p.x+p.y)*p.z); }
  float Noise(float3 p) { float3 i=floor(p),f=frac(p);f=f*f*(3-2*f);return lerp(lerp(lerp(Hash(i),Hash(i+float3(1,0,0)),f.x),lerp(Hash(i+float3(0,1,0)),Hash(i+float3(1,1,0)),f.x),f.y),lerp(lerp(Hash(i+float3(0,0,1)),Hash(i+float3(1,0,1)),f.x),lerp(Hash(i+float3(0,1,1)),Hash(i+1),f.x),f.y),f.z); }
  float3 Weights(float3 n) { float3 w=pow(abs(n),4);return w/max(dot(w,1),1e-5); }
  float2 UVX(float3 p,float3 n){return float2(-p.z*(n.x<0?-1:1),p.y);}
  float2 UVY(float3 p,float3 n){return float2(p.x,-p.z*(n.y<0?-1:1));}
  float2 UVZ(float3 p,float3 n){return float2(p.x*(n.z<0?-1:1),p.y);}
  float4 Map(TEXTURE2D_PARAM(tex,samp),float3 p,float3 n,float2 uv)
  {
   if(_Triplanar<.5)return SAMPLE_TEXTURE2D(tex,samp,uv);
   float3 w=Weights(n);return SAMPLE_TEXTURE2D(tex,samp,UVX(p,n))*w.x+SAMPLE_TEXTURE2D(tex,samp,UVY(p,n))*w.y+SAMPLE_TEXTURE2D(tex,samp,UVZ(p,n))*w.z;
  }
  float3 Decode(float4 c) {float3 n=UnpackNormalScale(c,1);n.y*=lerp(1,-1,_InvertGreen);return n;}
  float3 RNM(float3 a,float3 b) {a.z+=1;b.xy=-b.xy;return normalize(a*dot(a,b)/max(a.z,.001)-b);}
  float3 ProjectedNormal(TEXTURE2D_PARAM(tex,samp),float3 p,float3 n,float2 uv,float4 tangentWS)
  {
   if(_Triplanar<.5)
   {
    float3 t=normalize(tangentWS.xyz),b=normalize(cross(n,t))*tangentWS.w;
    float3 d=Decode(SAMPLE_TEXTURE2D(tex,samp,uv));return normalize(t*d.x+b*d.y+n*d.z);
   }
   float3 x=Decode(SAMPLE_TEXTURE2D(tex,samp,UVX(p,n))),y=Decode(SAMPLE_TEXTURE2D(tex,samp,UVY(p,n))),z=Decode(SAMPLE_TEXTURE2D(tex,samp,UVZ(p,n)));
   float3 w=Weights(n),sg=step(0,n)*2-1;
   // Surface gradients: flat maps yield the original mesh normal on every projection.
   float3 g=w.x*float3(0,x.y,-x.x*sg.x)/max(x.z,.05)+w.y*float3(y.x,0,-y.y*sg.y)/max(y.z,.05)+w.z*float3(z.x*sg.z,z.y,0)/max(z.z,.05);
   return normalize(n+g-n*dot(g,n));
  }
  float DetailFade(float3 p) {float footprint=max(length(ddx(p)),length(ddy(p)));return 1-smoothstep(.18,.8,footprint);}
  float Region(float3 p,int kind)
  {
   float3 f=frac(p)-.5;float h=Hash(floor(p));float radial=length(f.xy);
   if(kind==0)return Noise(p*.55);
   if(kind==1)return smoothstep(.35,.7,Noise(p*.3));
   if(kind==2)return 1-smoothstep(lerp(.24,.42,_Fabric.z),lerp(.45,.62,_Fabric.z),length(f));
   if(kind==3)return 1-smoothstep(.30,.43,length(f*float3(1,1.2,.8)));
   if(kind==4)return 1-smoothstep(.26,.42,max(abs(f.x+f.y*.4),max(abs(f.y-f.z*.3),abs(f.z+f.x*.2))));
   if(kind==5)return (1-smoothstep(.035,.09,abs(radial-.30)))*step(-.07,f.y)*( .65+.35*cos(atan2(f.y,f.x)*16));
   if(kind==6)return (1-smoothstep(.32,.44,radial))*(.55+.45*cos(radial*72));
   if(kind==7)return 1-smoothstep(.24,.4,max(abs(f.x),max(abs(f.y),abs(f.z))));
   if(kind==8)return 1-smoothstep(.14,.29,abs(f.x)+abs(f.y*.5+f.x*.7)+abs(f.z*.35));
   if(kind==9)return (1-smoothstep(.04,.1,abs(f.y)))*(1-smoothstep(.22,.44,length(f.xz)));
   if(kind==10)return 1-smoothstep(_VeinWidth/max(_Spacing.z,.001),_VeinWidth/max(_Spacing.z,.001)+.04,abs(sin((p.x+Noise(p*.25)*.3)*3.14159)));
   if(kind==11)return 1-smoothstep(.16,.33,length(f));
   if(kind==12)return 1-smoothstep(.22,.42,length(f*float3(.7,4,1)));
   return .5+.5*sin(p.y*6.28318);
  }
  float Mask(float3 p,float4 layer)
  {
   if(layer.y<=0)return 0;
   int kind=(int)layer.x;
   float scale=max(layer.z,.00001);
   float3 q=p/scale;
   if(kind==10)q=p/max(_Spacing.z,.00001);
   if(kind==13)q=p/max(_Spacing.x,.00001);
   q.y*=lerp(1,4,_Fabric.x);q.x*=lerp(1,.55,_Fabric.x);
   if(kind==3||kind==4)q*=lerp(.65,1.5,Noise(q*.3)*_Fabric.y);
   float m=Region(q,kind);
   // Coverage zero is exactly identity, including normals and packed parameters.
   float cover=layer.y;
   if(kind==2||kind==6)cover=lerp(cover,max(cover,_Fabric.z),_Fabric.z);
   return saturate(m*cover)*DetailFade(q);
  }
  float3 Bump(float3 n,float3 p,float h,float strength)
  {
   float3 dx=ddx(p),dy=ddy(p),r1=cross(dy,n),r2=cross(n,dx);float det=dot(dx,r1);
   float3 grad=(ddx(h)*r1+ddy(h)*r2)*(det<0?-1:1)/max(abs(det),1e-7);
   return normalize(n-grad*strength);
  }
  struct RockSurface { float3 color,normal;float smooth,metal,ao; };
  RockSurface Surface(Varyings i)
  {
   RockSurface s=(RockSurface)0;
   float3 nWS=normalize(i.normalWS),n=(_WorldSpace>.5||_Triplanar<.5)?nWS:normalize(TransformWorldToObjectNormal(nWS));
   float3 original=(_WorldSpace>.5?i.positionWS:i.positionOS)-_GeoOrigin.xyz;
   float3 anchor=(_WorldSpace>.5?TransformObjectToWorld(float3(0,-.5,0)):float3(0,-.5,0))-_GeoOrigin.xyz;
   float3 density=_WorldSpace>.5?float3(_DisplayScale,_DisplayScale,_DisplayScale):float3(1,_DisplayScale,1);
   float3 p=(anchor+(original-anchor)*density)*_UnitMetres;
   float3 texturePosition=p;
   float3 macro=texturePosition/max(_MacroScale,.00001),grain=texturePosition/max(_GrainScale,.00001);
   grain*=lerp(float3(1,1,1),float3(.55,4,1),_Fabric.x);
   float fade=DetailFade(grain)*_DetailEnabled;
   s.color=Map(TEXTURE2D_ARGS(_BaseMap,sampler_BaseMap),macro,n,(i.uv*float2(1,_DisplayScale))/max(_MacroScale,.00001)).rgb*_Tint.rgb;
   s.color*=lerp(.94,1.06,Noise(p/max(_Spacing.w,.00001)));
   float4 detail=Map(TEXTURE2D_ARGS(_DetailMap,sampler_DetailMap),grain,n,(i.uv*float2(1,_DisplayScale))/max(_GrainScale,.00001));
   float4 regions=Map(TEXTURE2D_ARGS(_MineralMask,sampler_MineralMask),grain,n,(i.uv*float2(1,_DisplayScale))/max(_GrainScale,.00001));
   s.color*=lerp(1,detail.rgb*1.65,fade*_DetailStrength);
   s.color=lerp(s.color,s.color*_MineralLight.rgb*1.3,regions.r*fade*.55);
   float darkRegion=lerp(regions.g,1-smoothstep(_MineralBalance-.03,_MineralBalance+.03,regions.b),_HasMineralRegions);
   s.color=lerp(s.color,s.color*_MineralDark.rgb*2,darkRegion*fade*.8);
   float4 packed=Map(TEXTURE2D_ARGS(_SurfaceMap,sampler_SurfaceMap),macro,n,(i.uv*float2(1,_DisplayScale))/max(_MacroScale,.00001));
   s.metal=0; // Bulk host is dielectric. Only explicit component masks can be metallic.
   s.ao=lerp(1,packed.g,_HasSurface);s.smooth=saturate(_Smoothness*lerp(1,packed.a*2,_HasSurface));
   s.smooth=lerp(s.smooth,saturate(s.smooth+.06),(1-_Fabric.w)*_Fabric.z); // Cement between supported grains, never assumed open voids.
   float3 normal=n;
   if(_HasNormal>.5)normal=ProjectedNormal(TEXTURE2D_ARGS(_NormalMap,sampler_NormalMap),macro,n,(i.uv*float2(1,_DisplayScale))/max(_MacroScale,.00001),i.tangentWS);
   if(_HasDetailNormal>.5)
   {
    float3 dn=ProjectedNormal(TEXTURE2D_ARGS(_DetailNormal,sampler_DetailNormal),grain,n,(i.uv*float2(1,_DisplayScale))/max(_GrainScale,.00001),i.tangentWS);
    // RNM in an orthonormal local surface frame, then return to projection space.
    float3 t=normalize(cross(abs(n.y)<.95?float3(0,1,0):float3(1,0,0),n)),b=cross(n,t);
    float3 a=float3(dot(normal,t),dot(normal,b),dot(normal,n)),d=float3(dot(dn,t),dot(dn,b),dot(dn,n));
    float3 rn=RNM(a,d);normal=normalize(lerp(normal,t*rn.x+b*rn.y+n*rn.z,fade));
   }
   normal=normalize(lerp(n,normal,_NormalStrength));
   s.normal=_WorldSpace>.5||_Triplanar<.5?normal:TransformObjectToWorldNormal(normal);
   float warp=Noise(p/max(_Spacing.w,.00001))*_Irregularity;
   float bed=.5+.5*sin((dot(p,normalize(_BeddingNormal.xyz))/max(_Spacing.x,.00001)+warp)*6.28318);
   float cleave=pow(.5+.5*cos(dot(p,normalize(_CleavageNormal.xyz))/max(_Spacing.y,.00001)*6.28318),12);
   float fracture=pow(1-abs(sin((p.x+p.z*.43+warp)/max(_Spacing.z,.00001)*3.14159)),22);
   s.color*=1-_Structure.x*bed*.28-_Structure.y*cleave*.35-_Structure.z*fracture*.42;
   s.color=lerp(s.color,s.color*lerp(.5,1.3,bed),_Structure.w);
   float h=-_Structure.x*bed*.0008-_Structure.y*cleave*.001-_Structure.z*fracture*.002;
   [unroll]for(int k=0;k<2;k++)if(k<_LayerCount)
   {
    float4 layer=k==0?_Layer1:_Layer2,ls=k==0?_LayerSurface1:_LayerSurface2;
    float3 color=k==0?_LayerColor1.rgb:_LayerColor2.rgb;float mask=Mask(p,layer);
    if(_HasCoverageMask>.5)mask*=Map(TEXTURE2D_ARGS(_CoverageMask,sampler_CoverageMask),macro,n,(i.uv*float2(1,_DisplayScale))/max(_MacroScale,.00001)).r;
    float3 cn=s.normal;float smooth=ls.x,metal=ls.y,ao=1;
    if(ls.z>.5)
    {
     float3 cp=texturePosition/max(layer.z,.00001);float2 cu=(i.uv*float2(1,_DisplayScale))/max(layer.z,.00001);float4 data=float4(0,1,0,.5);
     if(k==0) { color*=Map(TEXTURE2D_ARGS(_ComponentMap1,sampler_ComponentMap1),cp,n,cu).rgb;cn=ProjectedNormal(TEXTURE2D_ARGS(_ComponentNormal1,sampler_ComponentNormal1),cp,n,cu,i.tangentWS);data=Map(TEXTURE2D_ARGS(_ComponentSurface1,sampler_ComponentSurface1),cp,n,cu); }
     else { color*=Map(TEXTURE2D_ARGS(_ComponentMap2,sampler_ComponentMap2),cp,n,cu).rgb;cn=ProjectedNormal(TEXTURE2D_ARGS(_ComponentNormal2,sampler_ComponentNormal2),cp,n,cu,i.tangentWS);data=Map(TEXTURE2D_ARGS(_ComponentSurface2,sampler_ComponentSurface2),cp,n,cu); }
     if(_WorldSpace<.5&&_Triplanar>.5)cn=TransformObjectToWorldNormal(cn);
     smooth*=data.a*2;ao=data.g;metal=max(metal,data.r);
    }
    s.color=lerp(s.color,color,mask);s.smooth=lerp(s.smooth,smooth,mask);s.metal=lerp(s.metal,metal,mask);s.ao=lerp(s.ao,ao,mask);
    cn=normalize(lerp(nWS,cn,_NormalStrength));s.normal=normalize(lerp(s.normal,cn,mask));h+=mask*layer.w*.0015;
   }
   s.normal=Bump(s.normal,i.positionWS,h/max(_DisplayScale,.000001),_NormalStrength);
   if(_ProceduralDark>.5)s.color=lerp(s.color,float3(.025,.018,.012),.95)*lerp(.85,1.1,Noise(p/max(_Spacing.w,.00001))); // Explicit tar/bitumen role.
   if(_Legend>.5||_Schematic>.5)
   {
    float3 q=p/max(.12,_Spacing.x);float pat=step(.65,frac(q.x+q.y*(1+fmod(_StatusPattern,3))));
    if(_StatusPattern==2)pat=max(step(.8,frac(q.x)),step(.8,frac(q.y)));
    if(_StatusPattern==3)pat=step(.72,frac(q.x-q.y));
    if(_StatusPattern==4)pat=step(.7,Noise(q*2));
    s.color=_LegendColor.rgb*lerp(.6,1.15,pat);s.normal=nWS;s.smooth=.12;s.metal=0;s.ao=1;
   }
   if(_StatusPattern==5)s.color=lerp(s.color,float3(.95,.4,.1),step(.8,frac((p.x+p.y)*10))*.35);
   return s;
  }
  half4 Frag(Varyings i):SV_Target
  {
   UNITY_SETUP_INSTANCE_ID(i);UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
   RockSurface s=Surface(i);InputData input=(InputData)0;input.positionWS=i.positionWS;input.positionCS=i.positionCS;input.normalWS=s.normal;
   input.viewDirectionWS=GetWorldSpaceNormalizeViewDir(i.positionWS);input.shadowCoord=TransformWorldToShadowCoord(i.positionWS);
   input.bakedGI=SampleSH(s.normal);input.normalizedScreenSpaceUV=GetNormalizedScreenSpaceUV(i.positionCS);input.shadowMask=half4(1,1,1,1);
   input.vertexLighting=VertexLighting(i.positionWS,s.normal);
   SurfaceData sd=(SurfaceData)0;sd.albedo=s.color;sd.alpha=1;sd.normalTS=half3(0,0,1);sd.metallic=s.metal;sd.smoothness=s.smooth;sd.occlusion=s.ao;
   half4 c=UniversalFragmentPBR(input,sd);c.rgb=MixFog(c.rgb,i.fog);return c;
  }
  half4 DepthFrag(Varyings i):SV_Target {return 0;}
  half4 NormalFrag(Varyings i):SV_Target
  {
   RockSurface s=Surface(i);
   #if defined(_GBUFFER_NORMALS_OCT)
    float2 oct=PackNormalOctQuadEncode(s.normal);return half4(PackFloat2To888(saturate(oct*.5+.5)),0);
   #else
    return half4(s.normal,0);
   #endif
  }
  float3 _LightDirection;float3 _LightPosition;
  Varyings ShadowVert(Attributes a)
  {
   Varyings o=Vert(a);
   #if defined(_CASTING_PUNCTUAL_LIGHT_SHADOW)
    float3 ld=normalize(_LightPosition-o.positionWS);
   #else
    float3 ld=_LightDirection;
   #endif
   o.positionCS=ApplyShadowClamping(TransformWorldToHClip(ApplyShadowBias(o.positionWS,o.normalWS,ld)));return o;
  }
  ENDHLSL
  Pass
  {
   Name "ForwardLit" Tags {"LightMode"="UniversalForwardOnly"}
   HLSLPROGRAM
   #pragma target 3.5
   #pragma vertex Vert
   #pragma fragment Frag
   #pragma multi_compile_instancing
   #pragma multi_compile_fog
   #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN
   #pragma multi_compile _ _ADDITIONAL_LIGHTS_VERTEX _ADDITIONAL_LIGHTS
   #pragma multi_compile_fragment _ _ADDITIONAL_LIGHT_SHADOWS
   #pragma multi_compile_fragment _ _SHADOWS_SOFT _SHADOWS_SOFT_LOW _SHADOWS_SOFT_MEDIUM _SHADOWS_SOFT_HIGH
   ENDHLSL
  }
  Pass
  {
   Name "ShadowCaster" Tags {"LightMode"="ShadowCaster"} ColorMask 0 ZWrite On
   HLSLPROGRAM
   #pragma target 3.5
   #pragma vertex ShadowVert
   #pragma fragment DepthFrag
   #pragma multi_compile_instancing
   #pragma multi_compile_vertex _ _CASTING_PUNCTUAL_LIGHT_SHADOW
   ENDHLSL
  }
  Pass
  {
   Name "DepthOnly" Tags {"LightMode"="DepthOnly"} ColorMask R ZWrite On
   HLSLPROGRAM
   #pragma target 3.5
   #pragma vertex Vert
   #pragma fragment DepthFrag
   #pragma multi_compile_instancing
   ENDHLSL
  }
  Pass
  {
   Name "DepthNormals" Tags {"LightMode"="DepthNormalsOnly"} ZWrite On
   HLSLPROGRAM
   #pragma target 3.5
   #pragma vertex Vert
   #pragma fragment NormalFrag
   #pragma multi_compile_instancing
   #pragma multi_compile_fragment _ _GBUFFER_NORMALS_OCT
   ENDHLSL
  }
 }
 Fallback "Hidden/Universal Render Pipeline/FallbackError"
}
